from nightwatch import location, term
from nightwatch.c_dsl import Expr
from nightwatch.generator.c.buffer_handling import get_buffer
from nightwatch.generator.c.callee import (
    convert_input_for_argument,
    record_call_metadata,
    record_argument_metadata,
    log_call_declaration,
    log_ret_declaration,
)
from nightwatch.generator.c.stubs import call_function_wrapper
from nightwatch.generator.c.util import for_all_elements, AllocList
from nightwatch.generator.common import lines, comment_block
from nightwatch.model import Type, Argument, ConditionalType, Function, FunctionPointer, API
from typing import List


def replay_command_implementation(f: Function):
    with location(f"at {term.yellow(str(f.name))}", f.location):
        alloc_list = AllocList(f)
        return f"""
        case {f.call_id_spelling}: {{\
            {alloc_list.alloc}
            ava_is_in = 1; ava_is_out = 0;
            __cmd = __call_cmd;
            struct {f.call_spelling}* __call = (struct {f.call_spelling}*)__call_cmd;
            assert(__call->base.api_id == {f.api.number_spelling});
            assert(__call->base.command_size == sizeof(struct {f.call_spelling}) && "Command size does not match ID. (Can be caused by incorrectly computed buffer sizes, expecially using `strlen(s)` instead of `strlen(s)+1`)");
        
            /* Unpack and translate arguments */
            {lines(convert_input_for_argument(a, "__call") for a in f.arguments)}
        
            /* Perform Call */
            {call_function_wrapper(f)}
        
            ava_is_in = 0; ava_is_out = 1;
            __cmd = __ret_cmd;
            struct {f.ret_spelling}* __ret = (struct {f.ret_spelling}*)__ret_cmd;
            assert(__ret->base.api_id == {f.api.number_spelling});
            assert(__ret->base.command_size == sizeof(struct {f.ret_spelling}) && "Command size does not match ID. (Can be caused by incorrectly computed buffer sizes, expecially using `strlen(s)` instead of `strlen(s)+1`)");
            assert(__ret->base.command_id == {f.ret_id_spelling});
            assert(__ret->__call_id == __call->__call_id);

            /* Assign original handle IDs */
            {assign_original_handle_for_argument(f.return_value, "__ret") if not f.return_value.type.is_void else ""}
            {lines(assign_original_handle_for_argument(a, "__ret") for a in f.arguments if a.type.contains_buffer)}

            #ifdef AVA_RECORD_REPLAY
            {log_call_declaration}
            {log_ret_declaration}
            {lines(
            record_argument_metadata(a, src="__ret" if a.type.contains_buffer and a.output else "__call") for a in f.arguments)}
            {record_argument_metadata(f.return_value, "__ret") if not f.return_value.type.is_void else ""}
            {record_call_metadata("NULL", None) if f.object_record else ""}
            #endif

            {alloc_list.dealloc}
            break;
        }}
        """.strip()


def assign_original_handle_for_argument(arg: Argument, original: str):
    def convert_result_value(values, type: Type, depth, original_type=None, **other) -> str:
        if isinstance(type, ConditionalType):
            return Expr(type.predicate).if_then_else(
                convert_result_value(values, type.then_type, depth, original_type=type.original_type, **other),
                convert_result_value(values, type.else_type, depth, original_type=type.original_type, **other),
            )

        if type.is_void:
            return """abort_with_reason("Reached code to handle void value.");"""

        original_value, local_value = values
        buffer_pred = (
            Expr(type.transfer).equals("NW_BUFFER") & Expr(local_value).not_equals("NULL") & (Expr(type.buffer) > 0)
        )

        def simple_buffer_case():
            return ""

        def buffer_case():
            if not hasattr(type, "pointee"):
                return """abort_with_reason("Reached code to handle buffer in non-pointer type.");"""
            tmp_name = f"__tmp_{arg.name}_{depth}"
            inner_values = (tmp_name, local_value)
            return buffer_pred.if_then_else(
                f"""
                {type.nonconst.attach_to(tmp_name)}; 
                {get_buffer(tmp_name, original_value, type, original_type=original_type)}
                {for_all_elements(inner_values, type, depth=depth, original_type=original_type, **other)}
                """
            )

        def default_case():
            return (
                Expr(type.transfer)
                .equals("NW_HANDLE")
                .if_then_else(
                    (~Expr(type.deallocates)).if_then_else(
                        f"nw_handle_pool_assign_handle(handle_pool, (void*){original_value}, (void*){local_value});"
                    ),
                    (
                        (Expr(arg.ret) | Expr(type.transfer).equals("NW_OPAQUE"))
                        & Expr(not isinstance(type, FunctionPointer))
                    ).if_then_else(f"assert({original_value} == {local_value});"),
                )
            )

        if type.fields:
            return for_all_elements(values, type, depth=depth, original_type=original_type, **other)
        return (
            type.is_simple_buffer()
            .if_then_else(
                simple_buffer_case, Expr(type.transfer).equals("NW_BUFFER").if_then_else(buffer_case, default_case)
            )
            .scope()
        )

    with location(f"at {term.yellow(str(arg.name))}", arg.location):
        conv = convert_result_value(
            (f"{original}->{arg.param_spelling}", f"{arg.name}"),
            arg.type,
            depth=0,
            name=arg.name,
            kernel=convert_result_value,
            self_index=1,
        )
        return (Expr(arg.output) | arg.ret).if_then_else(comment_block(f"Assign or check: {arg}", conv))


def replay_command_function(api: API, calls: List[Function]) -> str:
    function_name = f"__replay_command_{api.identifier.lower()}"
    return f"""
    void {function_name}(struct command_channel* __chan, struct nw_handle_pool* handle_pool,
            struct command_channel* __log, 
            const struct command_base* __call_cmd, const struct command_base* __ret_cmd) {{
        int ava_is_in, ava_is_out;
        struct command_base const * __cmd;
        switch (__call_cmd->command_id) {{
        {lines(replay_command_implementation(f) for f in calls)}
        default:
            abort_with_reason("Received unsupported command");
        }} // switch
    }}
    """.strip()
