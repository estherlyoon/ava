clang_version = 7


def _config_clang():
    from pathlib import Path
    import sys

    here = Path(__file__)
    llvm_build_python = (here / ".." / ".." / ".." / ".." / ".." / "llvm" / "clang" / "bindings" / "python").resolve()
    if llvm_build_python.exists():
        sys.path.insert(0, str(llvm_build_python))
    from clang import cindex

    llvm_build_lib = (here / ".." / ".." / ".." / ".." / ".." / "llvm" / "build" / "lib").resolve()
    clang_so_names = [s.format(clang_version) for s in ["libclang.so.{}", "libclang-{}.so"]]
    for name in [str(llvm_build_lib / s) for s in clang_so_names] + clang_so_names:
        cindex.Config.set_library_file(name)
        try:
            cindex.conf.get_cindex_library()
            break
        except cindex.LibclangError:
            continue


_config_clang()

from clang.cindex import *
from clang import cindex
from ...extension import *
from glob import glob
import logging

logger = logging.getLogger(__name__)

clang_directories = glob("/usr/lib*/llvm-{v}/lib/clang/{v}*/include".format(v=clang_version)) + glob(
    "/usr/lib*/clang/{v}*/include".format(v=clang_version)
)
clang_flags = (
    [
        "-Wno-return-type",
        # "-Wno-implicit-function-declaration",
        "-Wno-empty-body",
        "-nobuiltininc",
    ]
    + [a for d in clang_directories for a in ["-isystem", d]]
    + ["-I../worker/include"]
)


@extension(Type)
class _TypeExtension:
    def is_pointer(self):
        return self.get_canonical().kind in (TypeKind.INCOMPLETEARRAY, TypeKind.VARIABLEARRAY, TypeKind.POINTER)

    def is_static_array(self):
        return self.get_canonical().kind in (TypeKind.CONSTANTARRAY,)

    def is_data_pointer(self):
        return self.is_pointer() and self.get_pointee().kind not in (TypeKind.FUNCTIONPROTO, TypeKind.FUNCTIONNOPROTO)

    def is_function_pointer(self):
        return self.is_pointer() and self.get_pointee().kind in (TypeKind.FUNCTIONPROTO, TypeKind.FUNCTIONNOPROTO)

    @property
    def expanded(self):
        """
        :return: the current type expanded **once** and only at the top-level type.

        This will expand `typedef`s, but only at the outermost type, so expanding `T*` will not expand `T`.
        """
        if self.kind == TypeKind.TYPEDEF:
            return self.get_declaration().underlying_typedef_type
        else:
            return self

    _original_get_pointee = Type.get_pointee

    @replace
    def get_pointee(self):
        pointee = self.expanded._original_get_pointee()
        if pointee.kind == TypeKind.INVALID:
            return self.element_type
        return pointee


@extension(TranslationUnit)
class _TranslationUnitExtension:
    def _get_file_content(self, file):
        if not hasattr(self, "_file_contents"):
            self._file_contents = {}
        if file.name not in self._file_contents:
            with open(file.name, "rb") as fi:
                self._file_contents[file.name] = fi.read()
        return self._file_contents[file.name]


@extension(Cursor)
class _CursorExtension:
    def find_descendants(self, pred):
        """
        Iterate the outermost descendants for which `pred` is true.
        """
        if pred(self):
            yield self
        else:
            for child in self.get_children():
                yield from child.find_descendants(pred)

    def find_all_descendants(self, pred):
        """
        Iterate all the descendants for which `pred` is true.
        """
        if pred(self):
            yield self
        for child in self.get_children():
            yield from child.find_all_descendants(pred)

    @property
    def referenced_parameters(self):
        def is_parameter(c):
            return (
                c.kind == CursorKind.DECL_REF_EXPR
                and c.get_definition()
                and c.get_definition().kind == CursorKind.PARM_DECL
            )

        refs = [c.displayname for c in self.find_all_descendants(is_parameter)]
        return refs

    @property
    def source(self):
        tu = self.translation_unit
        file = self.location.file
        s = self.extent.start.offset
        e = self.extent.end.offset
        res = str(tu._get_file_content(file)[s:e], encoding="utf-8")
        # if not res:
        #     print((file, s, e))
        return res

    @property
    def referenced_name(self):
        tu = self.translation_unit
        file = self.location.file
        range = self.referenced_name_range
        s = range.start.offset
        e = range.end.offset
        res = str(tu._get_file_content(file)[s:e], encoding="utf-8")
        # if not res:
        #     print((file, s, e))
        return res

    @property
    def children(self):
        if not hasattr(self, "_children"):
            self._children = tuple(self.get_children())
        return self._children

    @property
    def tokens(self):
        if not hasattr(self, "_tokens"):
            self._tokens = tuple(self.get_tokens())
        return self._tokens

    # @property
    # def deep_spelling(self):
    #     if self.spelling:
    #         return self.spelling
    #     return " ".join(t.deep_spelling for t in self.get_children()).strip()

    @property
    def untokenized(self):
        return " ".join(t.spelling for t in self.get_tokens())

    def _unparse_expression(self):
        # logger.info(("_unparse_expression", self.kind, self.source, self.spelling, self.untokenized,
        # self.displayname, self.mangled_name, self.canonical.spelling, self.referenced))
        if self.kind == CursorKind.CALL_EXPR:
            if len(self.children) > 1:
                args = ", ".join(c._unparse_expression() for c in self.children[1:])
            else:
                args = ""
            return f"{self.spelling}({args})"
        elif self.kind == CursorKind.CXX_UNARY_EXPR:
            return self.untokenized
        elif self.kind == CursorKind.DECL_REF_EXPR:
            # print(self.kind, self.spelling)
            return self.spelling
        elif self.kind == CursorKind.MEMBER_REF_EXPR:
            (v,) = [c._unparse_expression() for c in self.children]
            accessor = self.tokens[-2].spelling
            return f"{v}{accessor}{self.spelling}"
        elif self.kind == CursorKind.STMT_EXPR:
            (c,) = self.children
            return f"({{ {c._unparse_expression()} }})"
        elif self.kind == CursorKind.PAREN_EXPR:
            (c,) = self.children
            return f"({c._unparse_expression()})"
        elif self.kind == CursorKind.CSTYLE_CAST_EXPR:
            expr = self.children[-1]
            return f"({self.type.spelling}){expr._unparse_expression()}"
        elif self.kind in (CursorKind.INTEGER_LITERAL, CursorKind.STRING_LITERAL):
            return self.spelling  # or self.untokenized
        elif self.kind == CursorKind.CONDITIONAL_OPERATOR:
            pred, then_, else_ = [c._unparse_expression() for c in self.children]
            return f"{pred} ? {then_} : {else_}"
        elif self.kind == CursorKind.BINARY_OPERATOR:
            l, r = [c._unparse_expression() for c in self.children]
            return f"{l} {self.spelling} {r}"
        elif self.kind == CursorKind.UNARY_OPERATOR:
            (v,) = [c._unparse_expression() for c in self.children]
            if len(self.tokens) > 0:
                return f"{self.tokens[0].spelling}{v}"
            else:
                return ""
        elif self.kind == CursorKind.ARRAY_SUBSCRIPT_EXPR:
            v, i = [c._unparse_expression() for c in self.children]
            return f"{v}[{i}]"
        elif self.kind == CursorKind.UNEXPOSED_EXPR:
            return " ".join(c._unparse_expression() for c in self.children)
        elif self.kind == CursorKind.INIT_LIST_EXPR:
            return "{" + ", ".join(c._unparse_expression() for c in self.children) + "}"
        elif self.kind == CursorKind.CXX_NULL_PTR_LITERAL_EXPR or self.kind == CursorKind.GNU_NULL_EXPR:
            return f"({self.source})"
        elif self.kind == CursorKind.NAMESPACE_REF:
            # TODO(yuhc): debug me.
            return f"{self.spelling}::"
        else:
            return f"""_Pragma("GCC error \\"{self.kind} not supported in specification expressions.\\"")"""

    @property
    def unparsed(self):
        if self.kind.is_expression():
            r = self._unparse_expression()
            return r
        else:
            return self.source or self.untokenized


@extension(File)
class _FileExtension:
    @property
    def source(self):
        tu = self._tu
        return str(tu._get_file_content(self), encoding="utf-8")
