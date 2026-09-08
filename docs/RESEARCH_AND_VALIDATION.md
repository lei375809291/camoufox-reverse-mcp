# 公开方案取舍与 Agent 验证方法

本轮不是按工具数量扩充功能。先检查固定公开源码/测试，再独立实现与本地验证；不把项目宣传、单测通过或 Agent 自述当作所有站点适用的证据。

## 来源与采用范围

- [Chrome DevTools MCP 的 NetworkFormatter](https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/bf9d0a4e94992ae5bf514f08a5d1465891d421d7/src/formatters/NetworkFormatter.ts) 与[保存正文测试](https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/bf9d0a4e94992ae5bf514f08a5d1465891d421d7/tests/formatters/NetworkFormatter.test.ts)：借鉴文件产物与摘要分离。本实现保存已捕获的实体字节，明确 SHA256、长度与截断，不自动补抓。
- [Playwright MCP 的工作流说明](https://github.com/microsoft/playwright-mcp/blob/8a13ef8e9f7385a0f89477922127f31cbfde9761/README.md)：借鉴按任务选择持久 MCP/本地脚本、按需读取信息。没有套用未经本项目测量的 token 降幅。
- [reverse-skill 的 JS 工作规范](https://github.com/zhaoxuya520/reverse-skill/blob/7e2097fd90d25c2f976f6eba26d6c00aa88051df/skills/js-reverse/SKILL.md)：借鉴实际工具定义、证据记录和最小补丁的思路，改为任务首检与相关状态变化复查，不复制固定客户端前缀、端口或全量必读路径。
- [js-reverse-mcp 的 WebSocketCollector 测试](https://github.com/zhizhuodemao/js-reverse-mcp/blob/bf7dc506e8743ba1ec5bd3325c91818b9192e40e/tests/WebSocketCollector.test.ts) 与 [DebuggerContext 测试](https://github.com/zhizhuodemao/js-reverse-mcp/blob/bf7dc506e8743ba1ec5bd3325c91818b9192e40e/tests/DebuggerContext.test.ts)：研究了有界证据和调试状态恢复，运行相关测试；涉及 Chrome/CDP 的能力需要 Firefox 适配，本版本不宣称已经迁移。
- [jshookmcp 的 AST 变换](https://github.com/vmoranv/jshookmcp/blob/55e22b706d937c3c56f3b48b883b98b524d4166d/src/server/domains/transform/handlers/ast-ops.ts)：合成探测确认浮点舍入、局部同名 atob 和 var 提升等改写风险。本版本只加入候选预览/语义验证指导，没有复制该实现或自动应用这些变换。

公开方案提供设计线索，新增代码在本项目独立实现。根许可已核对；没有将许可不明或强互惠许可的源码片段搬入本项目。

## 实操方式

每轮冻结 Skill/MCP 副本，给新的 Agent 自然语言任务与本地实验 URL，隐藏参考答案；每个浏览器任务通过真实 MCP stdio 的 initialize、tools/list、tools/call 操作独立 Camoufox。网络/工具参数、失败与进程日志保留。协议任务不强制启动浏览器。

客观验收包含：

- 签名：同 URL 并发请求的精确正文/响应关联；关闭浏览器后，由独立 Node 对新输入签名，再由独立 HTTP 客户端验收；修改正文或重复 query 后必须被拒绝。
- WASM：实际捕获来源、完整实体字节/SHA256、独立实例化和新参数调用；文本预览或截图不能替代模块。
- 采集：更换配置/字段/页大小，独立进程续采，验证业务拒绝、临时故障、重复游标、完整去重和完成后零请求。

维护者另外检查模型是否重复首检、误清理已有证据、猜工具参数、误把副作用或错误当成功。单独“日志一致性测试通过”不算产物验收通过。

## 实操发现与修正

- 第一轮产物通过，但深层文档仍有二次确认和通用五次请求要求；已按任务类型修正。
- 请求比较的摘要哈希容易被错认为实际 Body 字节哈希；已明确 JSON 序列化口径并补充 raw_utf8 字节指标。
- 分页任务暴露空中间页、临时重复、业务等待与累计/本次上限适配需求；配套 Skill 核心以可选项补齐，旧默认行为保留。
- 第二轮一个 WASM 任务受实验沙箱网络 EPERM 阻塞，失败记录保留；修正本地 loopback 配置后再用新任务/新 Agent 验证，未伪装成通过。
- 同一失败还暴露原始导航错误被掩盖、错误页快照等待过长和版本字段含义不明；MCP 增加明确的错误阶段、有界快照及应用版本标识。

## 验证边界

这是受控 loopback 服务、真实 Camoufox 和独立进程的行为验证，不是商业站点反检测成功率测试。浏览器实验使用 macOS 外层进程限制，并关闭 Firefox 内层 sandbox 以避免嵌套冲突；与普通部署有差异。日志不是防篡改认证，未验证无限规模、全部崩溃窗口或长期压测。通用能力仍需按具体接口的鉴权、签名与结束契约适配。

发布前必须完成新的实操复验、回归、schema 对齐和构建；每轮失败保留，不能只汇总成功样本。

## 本版本发布前结果

共三轮、九个新上下文 Agent 实操任务。第一轮三个产物通过；第二轮签名/分页通过，WASM 因实验访问限制阻塞（保留失败）；第三轮三个全新任务全部通过独立验收，包含此前受阻的 WASM。

| 轮次 | 签名 | WASM | 分页 | 反馈后处理 |
|---|---|---|---|---|
| 1 | 通过 | 通过 | 通过 | 修正文档重复阶段、验收范围、哈希口径；明确分页缺口 |
| 2 | 通过 | 未完成 | 通过 | 修正实验网络配置、导航/快照诊断与版本标识；完善可选分页能力 |
| 3 | 通过 | 通过 | 通过 | 独立新输入复验；补充 Unicode JSONL 消费及异步请求捕获窗口说明 |

三个成功的最终任务分别验证新的签名输入及篡改反例、71 字节模块的完整性/新参数调用、29 条记录的跨进程恢复和六类故障模式。各浏览器主流程只做一次任务环境检查，没有 reset；第二轮签名因自写 driver 在导航前出错而新建连接，重新检查有明确原因。失败与模型自写脚本的错误均未从记录中删除。

MCP 完整本地回归含真实浏览器：243 passed、1 skipped；默认离线集合为242 passed、2 skipped。配套 Skill 的40项Python模板测试、4项脚本/本地HTTP测试、12项Node测试及39工具契约验证通过。最终生产运行代码与第三轮冻结副本一致；之后仅补充说明、回归测试、CI和发布记录。
