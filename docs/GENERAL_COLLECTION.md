# 通用采集契约（v1.6.0）

所有能力基于请求/响应与显式样本，不包含目标站点特例。原有工具名、默认返回列表和默认浏览器验签路径保留。

## 采集窗口

```python
network_capture(action="start", capture_body=True, max_body_size=200000)
# navigate / click / evaluate_js 触发代表性操作
network_capture(action="stop", wait_timeout_ms=3000)
list_network_requests(limit=100, after_id=0)
get_network_request(request_id=1, include_body=True, max_body_size=-1)
```

- Request 对象唯一关联响应，支持同 URL 并发。Cookie/Set-Cookie 使用异步完整 headers，`headers_complete` 表示是否成功读取。
- stop 拒绝新请求进入采集窗口，已捕获的请求继续完成；有界等待不保证所有网络请求完成。检查 `pending_requests/pending_responses` 和每条记录的 `state/body_state`。
- clear、reset 与 close 取消当前实例后台采集任务；缓冲区仍为最多 2000 条，`dropped_requests` 显示淘汰数量。响应处理最多同时 32 个任务，超出时记录 `skipped_capacity`，避免无界队列。
- ID 在 close 前单调递增，clear/reset/navigation 不复用已发出的 ID。增量客户端同时核对 dropped 与 first_request_id，不能把游标分页理解为完整历史存储。
- 域名过滤使用完整 host/subdomain 边界，不会从查询参数中匹配域名或误匹配相似主机。
- body 保存上限按字符计，另外记录原始字节数。采集截断与返回截断分别表示，旧 `response_body_truncated` 为二者的或。`max_body_size=-1` 不会找回被采集上限丢弃的内容。
- Playwright 先读取完整响应再截取保存内容；该上限不是流式下载内存限制。大型下载不宜启用正文捕获。
- 请求发起栈仍依据 Hook 日志启发式定位，不能把本轮响应对象关联修复等同于调用栈精确归因。

## 导出

```python
export_network_capture(save_path="artifacts/capture-redacted.json")
# 确需原始凭据/查询/正文时显式选择，放在私有工作区
export_network_capture(save_path="artifacts/capture-private.json",
                       include_sensitive=True, include_body=True)
```

JSON 含 `schema_version=1`、MCP 版本、采集状态与请求条目。默认对全部 header/query 值掩码，去除 URL 用户信息、fragment 和正文，保留 URL path。因此它不是全量匿名化工具。原始样本应保持私有，写入前序列化且拒绝覆盖已存在文件。导出只是当前缓冲区快照，不会重放网络请求；pending、淘汰和截断状态随快照保留。

## Cookie 删除

`cookies(action="delete", name="sid", domain="example.test")` 要求两项同时匹配。每次只处理选中的 name/domain/path，域名可包含子域但不匹配相似域名；无过滤器仍表示清空全部。

Playwright 1.43+ 使用精确过滤删除；更老运行库只将选中 Cookie 设置为过期，不会先清空整个 Cookie jar。过滤参数引入版本见 [Playwright 官方文档](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-clear-cookies)。

## 独立验签

```python
verify_signer_offline(
    signer_code="async ({text}) => ({sign: require('node:crypto').createHash('md5').update(text).digest('hex')})",
    samples=[{"input": {"text": "abc"}, "expected": {"sign": "900150983cd24fb0d6963f7d28e17f72"}}],
    runtime="node",
    timeout_ms=10000,
)
```

- 签名函数接收 `sample.input` 本身；需固定时间/随机因素时把它们作为输入，不自动修改浏览器环境。
- browser 是默认运行时，使用当前页面独立词法作用域；node 显式启用，需要 Node.js，运行在可终止的独立进程，无浏览器启动或跨运行时兜底。
- Node runner 提供 `crypto/node:crypto`、Buffer、文本与 URL 编码能力；不提供任意 npm require。复杂 jsdom/SDK 项目应在自己的项目中运行测试。
- 输入最多 1000 个样本，Node 输入与输出各有 2 MB 上限，执行期限最多 120 秒。只运行有意在本机执行的代码，Node vm 不是安全隔离边界。
- 每个样本都要求非空 expected；比较字段必须存在于 expected；返回缺失、空值、错误类型、抛错、超时都不会被当作通过。first_divergence 也记录抛错等失败。
- browser timeout 只代表调用等待结束，不保证用户 JS 已停止，也不会撤销已发生副作用；不自动重放。

## 验证

默认 `pytest -q` 运行原有回归与新增通用 fixture。真实浏览器测试仅访问 loopback HTTP，需预先安装浏览器：

```bash
CAMOUFOX_COLLECTION_INTEGRATION=1 pytest -q tests/test_collection_browser.py
```

通用分页程序、JSONL 恢复与 SDK 基线由配套 [hello_js_reverse_skill](https://github.com/WhiteNightShadow/hello_js_reverse_skill) 提供，MCP 本身不恢复已移除的 Session/Assertion 数据模块。

## v1.7.0：请求证据与任务级诊断

`compare_network_requests(request_ids=[...])` 对 2..10 条已有捕获做差异比较，保留重复 query、顺序、原始编码和 Body 原文。常量只返回字段名，原值仍从请求详情读取；输出按字段数与预览长度限制，比较不依赖被截短的预览。

每个值的 `sha256_scope=canonical_json_utf8`、`length_unit=serialized_json_characters` 描述摘要口径。字符串另有 `raw_utf8.bytes/sha256`；与服务端 Body 哈希对照时用 `body.raw` 的 raw_utf8，不能把 JSON 引号/转义后的摘要拿来比较。

`save_response_body(request_id, save_path, allow_partial=False)` 保存已有响应的可逆字节表示，适合 JS/WASM/JSON/二进制；返回实体字节数和SHA256，默认拒绝截断，拒绝覆盖已有文件，不触发新请求。`size`/`response_body_total_size` 等旧字段仍为字符数，增加 size_unit/body_bytes 明确口径；字节是解压后的实体，不是压缩传输线长度。

`check_environment` 新增 review/task_readiness 与浏览器 instance_id。已有 Hook/捕获可能属于当前任务，不自动清理；指纹只是状态范围提示，不代表鉴权/SDK完全未变。任务开始检查一次，状态变化时只查相关项。

`navigate` 保留真正的 goto 错误，不因错误日志出现 waiting 字样就当作超时。`take_snapshot(timeout_ms=5000, max_nodes=1000)` 有界等待，标注 accessibility/DOM fallback 与截断；超时后读页面和网络失败信息，不自动重放。初始化协议中的 serverInfo.version 与 check_environment 统一为 MCP 应用版本。

请求发起栈增加 `match_confidence=heuristic/unavailable`；URL 匹配的 Hook 栈只是线索，不能当作同 URL 并发的精确归因。

公开方案取舍和多轮实操方法见 [RESEARCH_AND_VALIDATION.md](RESEARCH_AND_VALIDATION.md)。
