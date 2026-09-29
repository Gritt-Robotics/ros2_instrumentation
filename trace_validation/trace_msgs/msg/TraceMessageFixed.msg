std_msgs/Header header   # stamp set immediately before publish; secondary correlation key
uint64 trace_id          # ground-truth monotonic unique id
int32[1000000] payload   # 4mb fixed-size payload to vary message size / in-flight count
