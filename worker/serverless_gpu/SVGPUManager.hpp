#include "manager_service.hpp"
#include "manager_service.proto.h"

#include "scheduling/BaseScheduler.hpp"
#include "scheduling/RoundRobin.hpp"

using ava_manager::ManagerServiceServerBase;

class SVGPUManager : public ManagerServiceServerBase {
  public:
  SVGPUManager(uint32_t port, uint32_t worker_port_base, std::string worker_path, std::vector<std::string> &worker_argv,
            std::vector<std::string> &worker_env, uint16_t ngpus)
    : ManagerServiceServerBase(port, worker_port_base, worker_path, worker_argv, worker_env) {
      // TODO: choose scheduler somehow
      scheduler = new RoundRobin(ngpus);
    };

  BaseScheduler* scheduler;

  ava_proto::WorkerAssignReply HandleRequest(const ava_proto::WorkerAssignRequest &request) override;
};