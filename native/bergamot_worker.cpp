#include "translator/parser.h"
#include "translator/response_options.h"
#include "translator/service.h"

#include <future>
#include <iostream>
#include <string>

// Byte lengths preserve paragraph boundaries, newlines, and UTF-8 exactly.
static void respond(const char* status, const std::string& text) {
  std::cout << status << " " << text.size() << "\n";
  std::cout.write(text.data(), static_cast<std::streamsize>(text.size()));
  std::cout.flush();
}

int main(int argc, char* argv[]) {
  using namespace marian::bergamot;
  marian::setThrowExceptionOnAbort(true);
  try {
    ConfigParser<AsyncService> parser("NER Bergamot worker", false);
    parser.parseArgs(argc, argv);
    const auto& config = parser.getConfig();
    if (config.modelConfigPaths.size() != 1) {
      std::cerr << "Exactly one model configuration is required.\n";
      return 2;
    }
    AsyncService service(config.serviceConfig);
    auto model = service.createCompatibleModel(parseOptionsFromFilePath(config.modelConfigPaths.front()));
    ResponseOptions options;
    std::string header;
    while (std::getline(std::cin, header)) {
      if (header.empty() || header.size() > 10 || header.find_first_not_of("0123456789") != std::string::npos)
        throw std::runtime_error("Invalid request length.");
      const auto size = std::stoul(header);
      if (size > 16 * 1024 * 1024) throw std::runtime_error("Request exceeds 16 MiB.");
      std::string input(size, '\0');
      if (!std::cin.read(input.data(), static_cast<std::streamsize>(size)))
        throw std::runtime_error("Incomplete request.");
      std::promise<Response> promise;
      auto future = promise.get_future();
      service.translate(model, std::move(input), [&promise](Response&& response) {
        promise.set_value(std::move(response));
      }, options);
      respond("OK", future.get().target.text);
    }
    return 0;
  } catch (const std::exception& error) {
    respond("ERROR", error.what());
    return 1;
  }
}
