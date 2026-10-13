# Inject this target into Mozilla's existing inference build without rebuilding
# its dependencies in a second build tree.
if(PROJECT_NAME STREQUAL "bergamot_translator" AND NOT TARGET ner-bergamot-worker)
  add_executable(ner-bergamot-worker "${CMAKE_CURRENT_LIST_DIR}/bergamot_worker.cpp")
  target_compile_features(ner-bergamot-worker PRIVATE cxx_std_17)
  target_link_libraries(ner-bergamot-worker PRIVATE bergamot-translator-source)
endif()
