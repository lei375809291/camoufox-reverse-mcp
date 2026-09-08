# 真实上游源码与本地实战（2026-09-08）

本轮以固定 GitHub 源码生成真实产物，再用独立 Agent、MCP stdio、Camoufox 和独立 Node/Python 程序验证。业务请求、演示密钥与风险状态机均在 loopback 服务中模拟，不代表商业站点的风控通过率。先前公开 Skill/MCP 的方案研究见 [研究记录](RESEARCH_AND_VALIDATION.md)。

## 来源与采用方式

| 上游 | 固定源码 | 实际使用 |
|---|---|---|
| KProtect | [da4ea8a](https://github.com/yang-zhongtian/KProtect/tree/da4ea8a3095a14dbb7cdf3126cb9cc2e076fdf2a) | 原 TypeScript 编译器、assembler、解释器；真实字节码执行。GPL 原文件仅在外部测试资产中下载 |
| javascript-obfuscator 4.1.1 | [828a190](https://github.com/javascript-obfuscator/javascript-obfuscator/tree/828a190cf80a86227ef77be38e99aad9838aed70) | 固定 npm 官方包与完整源码，真实 CFF 产物；BSD-2-Clause |
| CryptoJS 4.2.0 | [808f499](https://github.com/brix/crypto-js/tree/808f499ec789fcd68416328a40b8735a5c962116) | 原始 SDK，AES-256-CBC/PKCS7、HMAC-SHA256 与独立实现交叉验证；MIT |
| FingerprintJS 5.2.0 | [e196578](https://github.com/fingerprintjs/fingerprintjs/tree/e196578ba35362fdf15647e013d66ac28b3c9fb5) | 原 TS 入口构建，monitoring=false；MIT |
| Acorn 8.15.0 | [6dc5374](https://github.com/acornjs/acorn/tree/6dc537416ad628b3959b3ff963fbdcfdb380e0a3) | 本地 Node 解析器，MCP 随包附带原分发文件、MIT 文本、来源与哈希；不执行输入源码 |

上游 VM 并不支持任意 JavaScript：实际发现未加引号的对象 key、成员 postfix increment 不支持，分数字面量也有编译语义差异。未修改上游编译器来掩盖问题；记录限制后选择支持的等价业务输入。包含一个未修改的上游子串用例及自写 Unicode/数值业务函数。CFF 对照组关闭平坦化，实际开启组存在 3 个 dispatcher、25 个 case；不是用手写 switch 代替真实混淆器。

## 多轮执行与客观验收

先保存原始产物和哈希，运行基线；冻结候选后，三个新上下文 Agent 分别处理 VM/CFF、加解密/本地风险协议、原生指纹追踪。每个浏览器任务使用独立真实 MCP 进程与浏览器。保留失败的命令、参数、工具响应和修复后的重试，不删除失败分支。该轮无额外 macOS 外层实验沙箱；原生追踪仍使用其要求的内容 sandbox 配置。

- VM/CFF 操作者只得到新的保护产物，提取真实 SDK 并实现独立 Node 调用。新增输入、参数修改与重复状态调用通过；采集 4875 条真实 VM PC/opcode 轨迹，对照实际字节码与 Node 执行序列；CFF 捕获 220 条实际分派日志。维护者另用 40 个未给 Agent 的输入，对照原业务实现验收全部通过，禁止网络/子进程的交付进程也能运行。首次独立比较曾因跨 realm Array 原型产生假失败，原记录保留，修正为交付契约规定的 JSON 值比较。
- 加解密操作者交付 Node 内置 crypto 客户端，动态获取演示配置、现场 challenge 与随机 nonce/IV。两轮各 35 项断言通过；末轮实际 74 个 HTTP 请求中 65 个成功、9 个拒绝。检查 MAC/密文篡改、时间窗、challenge/nonce 重放、指纹绑定及响应关联。维护者另外验证 8 个新 Unicode 输入，由独立 Python AES/HMAC 解密核对；7 类拒绝的响应哈希与服务端台账一致。演示密钥公开，仅验证协议与状态机的一致性。
- 指纹操作者使用真实 reverse.5 浏览器，读取 capability、控制 ack 与原生 JSONL。相同 profile 的 5 次 SDK 结果中 42 个组件的值/状态一致（34 有值、8 unavailable；不含耗时），29 个描述符无变化。检验 cap、clear/stop/reset 与多窗口。没有把 42 组件解释成 42 个有效高熵值，也没有把 JS 读取次数当作 native getter 次数。

上述反馈修复后，再冻结候选交给第四个新上下文 Agent。它完成 60 次真实 MCP 协议操作（其中 58 次工具调用，含两次自有浏览器关闭）、33 项保存证据断言，VM/CFF 各 5 个新输入对照原始版本；分别保存 3348 条 VM、40 条 CFF 和 4635 条子 Frame 选择性日志，未达到容量上限。验证同步异常、异常前状态修改、clear 后继续调用、preview 的 getter/toJSON/随机源副作用、json_ascii 的孤立代理项/BOM/空白及特殊值标签。维护者阅读验收逻辑并独立重跑 33/33 通过；失败的辅助比较脚本保留。新候选之后的运行代码仅追加显式 regex 非空过滤拒绝（两项回归），没有换掉实测解析器、tap、Hook 或传输实现。

维护者另外验证双实例原生追踪目录隔离：清理/关闭 A 后 B 保留文件并继续产生事件；官方浏览器明确拒绝原生追踪。原生版本按单次启动选择，用户持久 active 配置不变。

## 从实操反馈落地的修复

| 观察到的问题 | 修复与验证边界 |
|---|---|
| 页面已有 208 条 PC 记录，工具返回 0 | instrumentation.log 从页面主世界读取，可选同一 iframe；主世界 208/208、子 Frame 208/208，清空后可继续采集 |
| 插桩吞掉 getter 异常、改变 method getter/参数顺序、消耗随机源或读取对象 toJSON | 保留原异常、接收者和求值顺序；日志使用 primitive 预览、捕获 intrinsics、确定性抽样。46 个语义场景从 45 个差异到 0：36 个实际改写、10 个保守跳过。跳过不算插桩成功 |
| 现代 SDK 无法用 esprima 解析，regex 可能误改源码 | 可选本地 Node + 随包 Acorn；UTF-16 offset 转 Python code point。保守 regex 白名单；受过滤约束的 parse 失败不进行无等价过滤的降级 |
| 同 URL 动态脚本复用陈旧内容，错误路径可能重复请求 | 每次获取当前响应，按内容摘要缓存改写；失败回填已取得的原响应，不 continue_ 再发；保持当前状态和 headers |
| 属性过滤未限制 method call，this.bytecode 无日志 | 过滤同时作用方法名与静态对象路径；不猜测动态对象身份 |
| evaluate_js 清洗孤立代理项、BOM 与空白 | 可选 json_ascii 返回 ASCII JSON 文本；特殊数值需显式标签。序列化失败不自动重新执行表达式 |
| 函数同步异常没有 trace，日志可能递归/消耗随机数 | trace 记录 outcome/thrownValue/completion；保存原抛出值；preview 模式不枚举/强制转换对象；不观察 Promise 的 .then 或 settlement |
| 原生 popup width=640，而追踪后快照为 1440 | snapshot_context 明确当前活动页主 Frame、隔离世界、非事件窗口归属。快照不能冒充事件时值 |

源码插桩会改变源码和耗时；函数 Hook 也会改变函数身份，constructor/预先被修改的 intrinsics 等有边界。默认 hook serialization=json 为旧用法兼容，可能触发对象 getter/toJSON；需要低副作用对象观测时明确选 preview，得到占位而非完整 I/O。optional chain/private/super/with 等不安全区域保守跳过，不能承诺全程序等价或不可检测。

日志到 20000 条上限或原生 cap 时明确 possibly_capped，截断不等于完整执行序列。原生 77 个点属于 DOM/Web API，不是 VM opcode 追踪；空白窗口与 SDK 页面窗口须分开，后者包含页面初始化，不能把每个事件都归给 SDK。

## 公开复现

配套 Skill 提供 [prepare/validate 脚本](https://github.com/WhiteNightShadow/hello_js_reverse_skill/tree/v3.9.0/scripts/real_cases) 与 [操作说明](https://github.com/WhiteNightShadow/hello_js_reverse_skill/blob/v3.9.0/scripts/real_cases/README.md)。准备时才联网下载固定 archive 与 npm lock；校验哈希、保留完整源码/许可证，资产写入仓库外的新目录。MCP 安装和启动不会自动下载这些案例。

全新目录的在线下载、只读缓存两条准备路径均成功，生成的脚本字节一致；公开 validator 通过真实 MCP 重新验证 VM/CFF 原始与 AST 四种场景（每场 76 项）、CryptoJS/指纹原始与 AST 两种场景，以及可选定制浏览器原生追踪。仅 Node 自测通过不替代浏览器或 native 通过；缺少原生能力时不会用 JS Hook 假冒成功。

本地回归、最终独立操作复验和构建结果见 [v1.8.0 版本说明](releases/v1.8.0.md)。原始日志含本机路径、现场指纹与运行数据，仅保存在维护者评测目录；公开的是固定来源、复现程序、结论及其边界。
