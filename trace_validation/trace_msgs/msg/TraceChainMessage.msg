std_msgs/Header header          # stamp set immediately before this step's publish
uint64 trace_id                 # this step's own monotonic unique id
uint64 chain_id                 # stable across one chain traversal; equals step 0's trace_id
uint32 step_index               # 0 = origin publisher; +1 per relay step
uint64 upstream_trace_id        # trace_id of the message received from the previous step (0 at step 0)
std_msgs/Header upstream_header # header of the message received from the previous step (default at step 0)
int32[] payload                 # [0:len(upstream.payload)] copy of upstream payload, rest is this step's filler
