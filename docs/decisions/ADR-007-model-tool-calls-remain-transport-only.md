USE @oh-my-pi/pi-ai@18.1.2 native tools.

pi-ai owns:
- provider tool representation
- streamed tool-call transport
- argument normalization/validation helpers

Lilavel owns:
- authorization
- execution
- permissions
- side-effect policy
- tool lifecycle semantics

Tool calls/results are not canonical conversation history.
AgentSession/pi-agent-core is not adopted.