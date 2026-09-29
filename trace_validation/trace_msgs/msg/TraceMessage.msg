std_msgs/Header header   # stamp set immediately before publish; secondary correlation key
uint64 trace_id          # ground-truth monotonic unique id
int32[] payload          # variable-size payload to vary message size / in-flight count
