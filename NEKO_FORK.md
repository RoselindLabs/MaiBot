# Neko MaiBot maintenance fork

这是 [Mai-with-u/MaiBot](https://github.com/Mai-with-u/MaiBot) 的薄维护 fork，
保留完整上游历史、作者归属和原 GPL-3.0/第三方许可，不是独立原创或官方发行。
当前维护源码基线为 **1.4.3 / e5391507531706a8bfce011d55ed94628039f2de**；
首个维护基线 1.3.15/062d45cf 的历史与初始补丁提交保留。
软件包仍使用上游版本号，具体修订以 **upstream SHA + fork SHA** 识别；未向官方 PyPI 包名发布。

<a id="143-当前维护状态2026-11-10已上线"></a>
<a id="当前维护状态"></a>
## 1.4.3 当前维护状态（2026-11-12整理）

本节为当前维护依据；后文旧SHA、来源数量和配置版本仅是当批记录，不覆盖本表。
整理依据已有源码与实例发布记录，本轮不重新巡检生产、不重跑业务验证。

| 边界 | 当前依据 |
| --- | --- |
| 上游基线 | MaiBot1.4.3／`e5391507531706a8bfce011d55ed94628039f2de` |
| 最近已部署业务修订 | `121729aa1ad4c456e6cde9118d5c808b6b3e66d3`，11月11日N08可配置回落及匹配Dashboard共同上线；文档HEAD前移不构成新业务发布 |
| 依赖与数据 | SDK2.22.1／Dashboard1.10.3／主库48；主配置8.16.18、模型任务配置1.17.26、公共模型目录3.0.0，固定`uv.lock` |
| 来源登记 | 已部署修订为36个Python源＋锁共37项；N18源码候选增加3源，当前`neko-patches.json`纳管39源＋锁40项，仅公开源hash |
| Dashboard | 同业务修订构建；265资源，源码树与制品关联独立于后端CI记录，不能只更新宿主而服务旧dist |
| 配套QQ适配器 | [SnowLuma维护fork](https://github.com/RoselindLabs/maibot-snowluma-adapter)，当前配套已验1.3.0／`dad01a7`；保原插件ID，候选不冒充部署 |
| Telegram | Host N17候选已撤回且未上线；命令后缀修复转[独立适配器fork](https://github.com/RoselindLabs/maibot-telegram-adapter)，不重新加入宿主补丁集合 |

**维护导航**：[发布/迁移](#发布与迁移边界) · [验证/CI](#本地验证与-ci) · [上游同步](#分支与上游同步) · [补丁台账](#升级补丁台账稳定id) · [Focus迁移](#已确认行为选择及迁移边界) · [Host认证](#host-认证与运维接口合同) · [文件能力](#文件能力的选择)。

本文件维护fork行为合同、验证和同步边界。实例端口/PID、配置/名单、插件版本总表、备份路径及详细日期验收
归所属运维记录，不复制进公开fork；源码、文档、CI artifact和生产部署是不同状态。

### N18 共享Focus子代理观察修复（源码候选，未部署）

- `subagent.started`从工具调用metadata捕获派发时`focus_scope_id`；显式空值表示普通派发。成员执行会话、权限、任务管理器和结果交付保持原归属。
- 子代理全部生命周期仍记录在真实成员下，Dashboard先按成员累积，再按开始事件的不可变归属投影共享组；任务键包含成员与开始事件，工具卡定位携带成员＋调用ID。成员重启仍消费真实`session.start`，不复制组启动或按结束时当前组重新分配。
- 共享组`history.load`首页沿现有`subagent.history`补发成员生命周期，即便组时间线没有更多页。旧started缺少归属时，仅用保留账本中开始事件之后的组Planner工具结果项`session_id + tool_call_id`与`started.call_id`精确连接；同成员调用ID重复、多个组声称同一调用、缺Planner或缺started均不猜测。已有显式归属优先，补齐只在返回载荷中，不迁库、不建立任务存储。
- 查询限于既有账本保留上限；补发沿用500事件、前端每成员50任务/2000事件限制。超过窗口可能缺少开始事件而不展示，不能承诺恢复已裁剪历史。组历史支持不依赖当前成员名单，也不把成员全部历史归入组。
- 定向合同入口：`pytests/neko/test_focus_subagents.py`（真实SQLite账本、派发/停止和WS历史入口）及既有`dashboard/src/routes/monitor/use-maisaka-monitor.test.ts`（实时、乱序/重复补发、跨成员碰撞、改组与重启）。主代理已审阅归属与历史加载边界；未运行生产、收费模型或QQ探针，发布仍需同SHA门禁与匹配Dashboard制品。
- 本地验证：Windows隔离禁网络7项行为＋16项来源策略通过；固定Bun1.2.1复用依赖，监控Hook/子代理面板57项通过，应用与Vitest两份TypeScript检查通过。主代理标准宿主门禁674项全部通过；首轮漏传既有`NEKO_PLUGIN_FIXTURES`导致9项夹具读取失败，指定现成夹具后通过，未改业务或放宽门禁。新测试格式检查已补齐。复用监控测试时用同环境HEAD字节副本确认两条旧断言（已删除`background`字段、阶段快照不建立会话）原本失败，更新测试契约后通过，未改变阶段业务实现。Linux CI及新Dashboard制品另按精确提交记录，不把本地测试当已部署。

N17为已撤回的Telegram宿主候选，不复用其编号；新观察行为登记N18。

<a id="11月11日n08配置扩展源码候选未部署"></a>
### N08任务内可配置回落（11月11日已上线）

沿既有`model_task_config.<task>`新增`gemini_fallback_mode`和`gemini_fallback_model`，
模型任务配置按现有模板生成/迁移规则从1.17.25最小升至1.17.26；无新UpgradeHook。
旧配置缺省`legacy_grok`和空目标，保持原精确API标识及任务顺序。
`custom`只接受当前任务`model_list`内、以`ModelInfo.name`引用的有效`llm`模型；空、任务外、
不存在或非生成模型明确拒绝。`disabled`停止定向备用。非custom历史残留读入可兼容、运行忽略，
保存/默认模板/版本迁移写出时清空；恢复默认不保留custom作用。
当前配置校验、共享目录失效引用及跨Bot删除/类型改变保护共用同一目标检查，
保存继续走原事务锁、目录revision与热重载，任务继用仍整体继用TaskConfig。
N08只扩展目标筛选，不改PROHIBITED_CONTENT、显式单模型、流式块、原有限重试或目标上下文重建边界。
前端沿现有功能分配/任务编辑，字段合同同后端；不以新增设置为由改变实例模型、人设或供应商。
来源在原32项上增加5个配置/保存Python源；精确集合与hash只维护`neko-patches.json`。

**已完成验证与发布**：[同SHA CI42967800030](https://github.com/RoselindLabs/MaiBot/actions/runs/42967800030)
的Linux/Windows合同、源码包及ci-gate成功；此前Windows定向96项及完整禁网络667项属于本轮准备证据，
不是后续每次文档修改必须重跑的数量门槛。Dashboard为本地Bun1.2.1冻结安装、tsc/Vite、153测试及
1440/390px合成浏览器证据；`neko-ci.yml`不含Dashboard CI，不能称前端CI已覆盖。
实例发布核37项来源、265静态资源及模型任务schema，DB48检查通过，75.30秒就绪；没有真实模型/QQ/TG试发。
Dashboard源码树`d23624c94f990638046ea0780b2ca1985c7188ce`，dist265文件/8099623字节，
清单SHA256 `d3e539ee4c28556c1a2a97cec6c4be02af2fd6a5a81b928f8e79758d744d3e5b`。
后续只有构建输入一致且制品摘要匹配时才复用，不为测试或文档改动重复构建；具体实例证据另存运维登记。

<details>
<summary>历史：11月10日已上线快照（被上方11日业务修订接续）</summary>

### 11月10日已上线快照

本节优先于下方历史1.3.19准备与发布记录。SDK固定2.22.1，Dashboard固定1.10.3，
主库48、主配置8.16.18、模型任务配置仍1.17.25；公共模型目录版本3.0.0。
已验业务源码`68dfde8d003832f7c4c60905f27f6937a67d3051`已推送并同SHA上线。
[CI 42747352281](https://github.com/RoselindLabs/MaiBot/actions/runs/42747352281)双平台各629项通过，
源码包及同提交Dashboard构建/265静态资源已验。11插件合成Runner、私有Linux配置迁移及实例验收由私有运维记录管理；
合成测试不代表真实模型/QQ送达，后续纯文档HEAD前移不构成待部署业务。
来源仍31个Python源加锁共32项。`src/maisaka/focus/manager.py`保持官方字节、未纳入维护源清单，不加新实例空组全局兜底。

</details>

<details>
<summary>历史：1.4.3集成的逐项keep/port决定</summary>

### 本次逐项集成决定

- N01 **port**：真实对端/完整身份/发送先后/恢复去重，合入上游场景及Host架构。
- N02 **port**：发送回执保留；新增发送Hook中止时也报告此前明确成功分段，纯语音不引用。
- N03 **keep**：完整表达链20秒及外部取消不变，适配新回复准备返回值。
- N04 **port**：临时素材隔离、私聊192/96及原子单份摘要保留，采用上游摘要结构校验。
- N05 **port**：上游payload签名改变，显式传ToolEntry，opt-in身份与文件票据独立。
- N06 **keep**：uv新旧错误URL格式兼容，仍不用原生更新替代固定fork发布。
- N07 **keep**：有界脱敏Gemini诊断及失败快照。
- N08 **keep/extend**：首候选、结构化拦截保留，11日扩展任务内legacy_grok/custom/disabled设置；不改普通失败/流式边界或模型参数。
- N09 **port**：32MiB及协商媒体表沿新RPC封套移植，还原大小限制和legacy保持。
- N10 **partial/keep**：上游吸收浮点MSE，仍保单帧/非正上限验证。
- N11 **port**：统一入站队列后保失败时水位、8批只读原子恢复及失效成员隔离。
- N12 **port**：子代理证据交接提示沿新入口保留，不扩大工具权限。
- N13 **port**：新观察页保正式消息身份配对显示，不修改账本。
- N14 **port**：保持旧锁的98个其它包，新增上游必需imageio-ffmpeg0.8.1、pycryptodome3.24.1、qrcode8.2、rlottie-python1.3.8；双平台/源包门禁保留。
- N15 **port**：普通群/私两次消息打断保护不变；撤掉上游新增的仅群限制，不代开开关。
- N16 **port**：请求局部观察接组级监控，实时耗时及退出清理，不增加总期限。

</details>

### 已确认行为选择及迁移边界

用户明确选择采用1.4官方统一Focus入站：不再等待各成员独立防抖，可能更早启动请求，
不保留旧ready调度桥。新消息仍不取消在途Focus请求；失败必须等失败后的新消息/主动任务，
不因请求期间旧积压立即重试。最多8批按原消息条数恢复，全批成功且读集有效才提交；失败保输入和水位，
工具unknown不重放，无Token预算，不调整中转站、模型、人设、权限或用户自行设置的等待时间。

官方1.4空`focus_groups`意味着无显式共享组，旧版却表示global。
`scripts/neko_prepare_143.py --input <private-copy> --output <new-copy>`只处理有明确版本的旧副本：
版本<=8.15.68、focus_mode=true、focus_groups显式[]且私聊参与选择明确，才生成单一显式通配组；
原focus_on_private、whitelist、权限及其它字段不动。关闭、已有显式组、新实例配置不变；模糊旧条件拒绝。
输出必须新文件，输入不写，重复执行幂等。Host默认配置及运行时不加兼容标记/兜底。

**已确认采用上游摘要输出策略**：原官方`chat.context_compaction_summary_max_chars`
默认6000（1000–8000），同时约束提示和渲染后长度；1.4删除该字段/提示参数/长度拒绝，
保留严格状态结构、非空与禁工具响应。可能接纳更长摘要并增大后续输入，不承诺成本不变。
这不是N新增Token机制，8批/原消息条数/原子性仍保留；若实例显式依赖自定义字符限制，
不得默默丢弃，应在切换前报告并取得选择。本轮用户已同意移除默认6000字符上限；私有副本核验不写入公开仓。

用户已同意旧`advanced_sub_agent`替换；当前子代理入口为`src/maisaka/builtin_tool/subagent.py`，
配置为`planner_subagent`，不把旧名称当作现存源码符号。旧`agent_acp_computer`及EmojiCandidateMessage已随官方移除，回归不复活废弃API；
保摘要工具配对/失败不提交、真实TTS消费、引用/附件/投递次数等行为断言。
新Host/Worker运行契约由测试提供synthetic endpoint，网络审计继续禁止模型/QQ连接。

### Host 认证与运维接口合同

- 旧实例原地登记，config/data/plugins无需强制搬入bots目录；新增host/注册表、公共模型和认证状态必须进备份。
- 凭据从旧data/webui.json迁到host/webui.json，旧文件可能删除。只读access_token用于POST
  `/api/webui/auth/verify`的JSON `{token: ...}`，HTTP200仍需检查`valid===true`。
- 持有响应CookieJar，不能硬编码cookie名称。名称为`maibot_host_session_<workspace字符串sha256前12>`，
  HttpOnly、SameSite=Lax、path=/、8小时绝对期限，Secure沿配置；凭据和cookie不输出日志。
- GET `/api/webui/auth/check`或`/api/host/session`核会话；GET `/api/host/bots`返回bots及workspace。
  必须按登记directory精确识别实例并校验32位hex bot_id，不按昵称或默认第一条推断。
- 业务接口为`/api/host/bots/{bot_id}/proxy/api/webui/...`，插件加载状态用末尾`plugins/installed`。
  使用Host cookie，不拿旧Bearer访问Worker。proxy剥离cookie/authorization后走实例IPC。
- 非GET/HEAD/OPTIONS请求检查Origin/Fetch元数据；无Origin允许，存在时须满足同源规则。
  Host写接口（`/api/host/*`且非`/proxy/`）还要求`X-MaiBot-Host: 1`；没有独立CSRF token。
  禁止靠放宽认证/端口解决客户端不兼容。认证更新/重置/创建Bot不是就绪探针。
- 模型定义迁到host/model_catalog.toml，任务仍在实例config/model_config.toml；旧完整配置快照
  不能直接当新结构写回。主库46→48另有统计后台回填；回退须连同Host状态、旧环境/锁/dist恢复，
  新unknown和外部动作现场不得被旧快照抹掉。

## 当前源码与阅读入口

当前版本/已验业务修订只维护顶部状态表，实际维护HEAD由Git读取；实例运行SHA仍由来源登记确认。
读代码先看[稳定补丁台账](#升级补丁台账稳定id)，操作先读[发布/迁移](#发布与迁移边界)、
[验证](#本地验证与-ci)及[上游同步](#分支与上游同步)。下文首次交付时的旧数量/测试结果不充当现行状态。

## 维护差异

### 历史增量发布范围（2026-11-06，已被顶部1.4.3状态接续）

当批从`e4914f8f1`增加N16进度观察及Dashboard实时耗时，来源曾为31源＋锁32项；这是历史范围，
不是当前升级白名单。现行N16行为、测试及撤销条件归下方台账，实际未发布差异应比较`deployed..target`，
不把旧来源数量、准备期测试或文档前移变成自动补发任务。

### 升级补丁台账（稳定ID）

以下条目均为当前维护线保留或移植项；**N10浮点MSE已被上游吸收，单帧/非正上限仍保留**。表中路径相对仓库根；精确源文件集合/摘要以
`neko-patches.json`为准。ID跟随行为而非行号或版本；文件重构时更新入口，不重新编号。
源码统一使用`NEKO: Nxx slug — 原因/必须保留边界`短注释，沿所在源码的语言风格。
每个独立修改块标一次：新增入口、已有函数局部分支、初始化与清理点应就近可定位；
连续实现内不逐行堆标记，不用整文件BEGIN/END包围，不复制平行patch文件。
`pytests/neko/test_source_policy.py`从本表读取ID/slug，解析Python真实注释及模块/类/函数文档字符串，
禁止未知ID、slug错配及缺边界说明，已纳管Python源必须有标记；普通字符串/提示词不算标记。
同文件允许多个ID；`uv.lock`仅走原字节hash校验，Dashboard沿独立制品合同。
原门禁继续验证39源＋锁40项hash及上游`src`差异集合，不机械要求diff每行与标记配对。
逐ID核对维护标记、实现及测试入口；hash只能检测漂移，不能证明行为。
当前双平台门禁见顶部；本次纯文档核对不重跑全量行为测试，不把旧605项标记维护验收当当前结果。

| ID | 行为与合并入口 | 必须保留的验证 | 可撤销条件 |
| --- | --- | --- | --- |
| N01 identity-echo | 正式会话归属、自身回传去重：`src/chat/message_receive/{bot,chat_manager}.py`、`src/common/utils/utils_session.py`、`src/maisaka/{runtime,context/history}.py`、`src/platform_io/manager.py`、`src/plugin_runtime/{integration,host/supervisor}.py` | `pytests/neko/test_private_self_echo.py` | 上游完整覆盖平台/账号/scope/对端身份及回传先后顺序，配套适配器通过 |
| N02 reply-receipt | 精确回复回执和纯语音引用：`src/maisaka/chat_loop_service.py`、`src/maisaka/builtin_tool/reply.py` | `pytests/neko/test_reply_contracts.py`及固定TTS消费者 | 上游提供等价回执、语音不附引用，消费者同步迁移 |
| N03 expression-budget | 表达选择整段20秒协作预算：`src/chat/replyer/maisaka_generator_base.py` | `pytests/neko/test_reply_contracts.py::test_actual_expression_budget_preserves_success_and_external_cancellation`；`pytests/maisaka/test_replyer_expression_selection.py`覆盖表达选择接入 | 上游覆盖前置步骤与取消语义；单模型hard_timeout不等价 |
| N04 context-pressure | 临时素材隔离、私聊内部压力回收、单份摘要：`src/maisaka/context/{compaction,internal_pressure}.py`、`src/maisaka/reasoning_engine.py` | `pytests/neko/test_life_compaction.py`、`test_internal_history_pressure.py`及`pytests/maisaka/test_context_compaction.py` | 上游保真实IM/引用/闭合工具批次、失败不提交与跨聊天隔离 |
| N05 trusted-tools | 显式opt-in可信工具身份：`src/plugin_runtime/component_query.py` | `pytests/neko/test_tool_context.py`、`test_plugin_bridge.py` | 上游提供同等身份边界且Files消费者迁移完成 |
| N06 update-source | uv来源错误格式兼容：`src/common/self_update.py` | `pytests/neko/test_release_1319.py::test_update_index_attribution_accepts_reviewed_uv_error_formats`及`pytests/startup_test/test_self_update_dependency_sources.py` | 上游支持新旧错误格式且仍精确绑定索引 |
| N07 gemini-diagnostics | 有界失败诊断与快照：`src/llm_models/{gemini_failure_diagnostics,request_snapshot}.py` | `pytests/neko/test_gemini_failure_diagnostics.py` | 上游保脱敏、有界记录和原失败终态 |
| N08 gemini-fallback | 首候选解析、结构化拦截、任务内可配置备用：`src/llm_models/{exceptions,utils_model,model_client/gemini_client}.py`；`src/config/{config,model_configs,shared_models}.py`、`src/host/shared_models.py`、`src/webui/routers/config.py` | `pytests/neko/test_gemini_failure_diagnostics.py`，含真实保存/迁移、非法引用及跨Bot保护 | 上游同时满足候选解析、未输出/未锁模型、候选交集、三态配置与有限重试 |
| N09 rpc-media | 双端32MiB、协商媒体表/同图去重：`src/plugin_runtime/{transport/base,protocol/hook_media,protocol/envelope,host/rpc_server,runner/rpc_client}.py` | `pytests/neko/test_rpc_frame_limit.py`、`test_hook_media_wire.py` | 上游兼容旧端、限制还原帧大小、失败不重放 |
| N10 gif-frames | 浮点MSE已由上游吸收；保留单帧/非正上限：`src/common/utils/utils_image.py::ImageUtils.gif_2_static_image` | `pytests/neko/test_gif_static_image.py` | 上游覆盖uint8溢出、max_frames及阈值边界 |
| N11 focus-recovery | 请求失败等新输入恢复、失效成员隔离、无执行快照时有限只读摘要：`src/maisaka/focus/group_runtime.py`、`src/maisaka/chat_loop_service.py`、`src/maisaka/context/compaction.py` | `pytests/maisaka/test_focus_group_runtime.py`、`pytests/neko/test_focus_recovery.py` | 上游保新输入唤醒、有限摘要原子提交、失败保留输入、无自动重试循环及unknown不重放 |
| N12 subagent-evidence | 子代理来源与核实层级交接：`src/maisaka/builtin_tool/subagent.py` | 子代理提示人工核对、宿主契约；不以提示断言证明模型遵循 | 上游提示等价覆盖来源URL、证据层级及检索收尾 |
| N13 dashboard-echo | 群/私聊自身回传展示合并：`dashboard/src/routes/monitor/maisaka-monitor.tsx`的`getSelfEchoIdentity`及气泡投影 | 对应监控合成样本、前端类型/构建、实际静态hash；无独立N13自动测试文件 | 上游按正式消息身份合并，缺身份和无出站对应仍保留 |
| N14 runtime-lock | `uv.lock`、固定SDK/TTS测试fixture、CI与包门禁：`scripts/neko_*.py`、`pytests/neko/`、`.github/workflows/neko-ci.yml` | 完整契约、隔离源包导入、来源集合核对 | 不因上游删除锁撤销；需明确新的可复现依赖与验证策略 |
| N15 bounded-steering | 普通聊天有限消息打断：`src/maisaka/reasoning_engine.py`、配置说明`src/config/official_configs.py` | `pytests/neko/test_bounded_steering.py`实际循环、准备竞态、取消/失败清理、关闭开关 | 上游跨重请求限制消息打断且保留排队输入与外部取消；共享Focus合同不变 |
| N16 planner-progress | 请求局部进度：`src/llm_models/{request_progress,utils_model}.py`、`src/maisaka/{reasoning_engine,focus/group_runtime}.py`；UI：`dashboard/src/routes/monitor/maisaka-monitor.tsx`的`StageStatusContent`/`ToolExecutionElapsed` | `pytests/neko/test_planner_progress.py`并发隔离、完成/取消清理、观察失败不影响请求 | 上游展示请求/重试/候选切换及实时耗时，保原超时/候选策略 |
| N18 focus-subagents | 共享Focus子代理观察归属：`src/maisaka/builtin_tool/subagent.py`、`src/maisaka/monitor/{events,event_store}.py`、`src/webui/routers/websocket/unified.py`；UI：`dashboard/src/lib/maisaka-monitor-client.ts`及`dashboard/src/routes/monitor/{use-maisaka-monitor,subagent-panel-state,subagent-panel,maisaka-monitor}` | `pytests/neko/test_focus_subagents.py`、`dashboard/src/routes/monitor/use-maisaka-monitor.test.ts`；成员/调用精确关联、停止/改组/重启、实时和历史回放 | 上游覆盖不可变派发归属与成员身份、历史精确关联及跨成员任务碰撞，且保执行与权限边界 |

N16仅观察现有请求生命周期，ContextVar绑定本次普通/共享Planner，退出后继承上下文失效；不改等待参数、总期限、重试/切模型策略，不让摘要/Replyer覆盖Planner进度。前端复用现有elapsed组件，不增加服务端心跳。

**稳定入口补充（不依赖行号；测试与撤销条件见同ID表项）**：

- **N11迁移工具**：`scripts/neko_prepare_143.py::prepare_focus_groups`，验证在`pytests/neko/test_release_143.py`；仅处理旧配置副本，不是运行时ready桥，也不修改官方`src/maisaka/focus/manager.py`。
- **N16**：`observe_planner`/`report_progress`、`MaisakaReasoningEngine._run_planner_request`与Focus `_run_turn`接入，`FocusGroupRuntime._set_stage`/`_publish_monitor_stage`接官方组级监控；UI沿上表观察页，不沿旧独立状态页。
- **N08前端**：`dashboard/src/routes/config/model/geminiFallback.ts`、任务编辑`components/TaskAssignmentBoard.tsx`、
  `hooks/useModelConfig.ts`/`useModelAutoSave.ts`及`dashboard/src/lib/pack-api.ts`；保序列化保存、
  非法草稿阻止提交、模型重命名/删除引用保护和旧任务完整参数。浏览器合成夹具为`dashboard/tests/browser/gemini-fallback.*`。
- **N11**：`MaisakaChatLoopService.chat_loop_step`的历史归属记录/只读准备分支；
  `FocusCompactionTemplate`、`FocusRecoveryError`、`_build_compaction_request`、`build_context_summary`；
  `FocusGroupRuntime.__init__`、`state`、`is_member_valid`、`_set_stage`、`run`、`_run_turn`、
  `_build_references`、`_recovery_identity`、`_recover_context`。核查新输入水位、参考后接入压力、
  全批原子提交和失败撤快照；常规`_compact`失败丢旧原文合同与只读恢复不可混合。
- **N15**：`MaisakaReasoningEngine.__init__`、`_run_interruptible_planner`、`run_loop`的finally；
  `ChatReplyTimingConfig.planner_message_steering`字段说明。初始化/成功重置/消息中止计数/外部取消/
  service标记清理/回合清理一起核查；只改说明不改变开关默认值。
- **N01**补充`MaisakaReasoningEngine._ingest_messages`、`_build_history_message`，以及
  `self_message_identity`、`has_self_message`；恢复、发送回传和实时接入均保正式身份去重。
- **N04**：`_neko_internal_control`、`_neko_compaction_items`、`message_text_for_compaction`、
  `_build_compaction_request`、`plan_internal_history_pressure`、`HistoryGuards.unchanged`及
  `MaisakaReasoningEngine._compact_private_internal_history`/`_end_cycle`/`run_loop`。
- **N02/N05/N08/N09**相邻接入：`_notify_reply_delivery`/`_build_send_result`/`execute_reply`；
  `ComponentQueryService._build_trusted_tool_context`与`_host_context`注入分支；
  `_raise_if_prohibited_content`/`_check_stream_content_blocks`/`_normal_reply_shape`与`LLMOrchestrator`备用分支；
  `encode_frame`/`decode_frame`与`RPCServer`/`RPCClient`连接初始化、握手、收发及清理。

**每次正式tag合并的核对记录**：在集成提交说明中逐ID记`keep / port / retire`及上游SHA、差异入口和验证结果。
只在等价证据成立后retire；同时移除对应维护注释、更新来源hash/集合及行为文档，保历史提交。
一个文件可能承载多个ID，不能整文件选择ours/theirs。测试通过不代替确认上游是否改变了用户策略。

**三个版本分开看**：上游基线来自清单`upstream_commit`；维护HEAD来自Git；部署SHA来自实例来源登记。
部署后按`deployed..HEAD`列实际未发布差异，纯文档前移不列为待上线业务。本文不写自指HEAD或假定推送即部署。

### N15 普通快速反应有限打断（2026-11-06）

- 普通ReasoningEngine在有效Planner决策完成前最多因消息打断两次，计数跨bind和同轮重请求；之后请求不绑定消息中断，准备期间和在途新消息保留到既有下一边界处理。
- 成功请求重置预算；逻辑回合结束、异常或取消在finally清理。外部task取消仍有效，包括准备竞态中适配器将取消转为ReqAbortException的交错；没有给工具执行加重试或中断。
- 保现有防抖、@、wait、最大内部轮次与开关默认false；不新增配置字段或Token预算。共享Focus源码保持，仅配置描述澄清其强制流式、不提供普通消息打断/wait提前结束。
- 事件驱动合成测试运行真实run_loop、请求准备/中断/消息接入链与Controller，以无网络模型事件替身验证连续消息仍可完成决策、成功后恢复预算、准备竞态、外部取消、失败退出、消息保留和子任务结束。上下文IO、动作执行与监控为替身，不代表真实模型一定成功、QQ送达或线上问题已验收。
- 首次交付纳管配置描述源，当时30源＋锁31项；后续已随维护版本上线，当前集合见清单，不重放当时增量事务。
- 当批Windows Python3.12锁定环境与普通中断定向9项、隔离禁网络597项通过（含原Focus回归和固定TTS消费者）；当时未执行Linux CI或真实模型请求。后续双平台发布证明见顶部，不把旧准备阶段的“未执行CI”当现行阻塞。

### N12 子代理检索与证据交接（2026-11-05）

子代理提示补充：已有来源优先读取核实、重复搜索无新证据及时收尾、找图优先预览配图而非猜文件名；
最终网络结论保关键URL，区分搜索摘录/已读正文/已预览图片，不把不同格式当不同构图。
只调整子代理工作提示，模型、人设、权限、轮数和工具执行保持；提示不保证模型必然遵从。
新增`src/maisaka/builtin_tool/subagent.py`来源登记，当批29源＋锁30项；当前总数见顶部，实例状态以发布记录为准。

### N11 Focus 请求失败恢复（2026-11-05）

- 共享 Planner 在请求边界遇到内容拦截、HTTP/网络、超时、空响应或解析错误时，保存未判断输入，
  明确显示“请求失败”，等待失败后新消息或新的插件主动任务再继续；不立即循环重试旧请求。
- 已在途期间积累的输入保留，但不以旧防抖事件自动重试。取消、工具副作用及持久化错误不走该恢复路径，
  原unknown工具闭合/禁止重放保持；失败后收到新的Proactive任务可沿宿主Focus入口唤醒。
- 内存已无正式会话的成员不进入工具路由或参考构造，避免有效性检查抛异常拖停整组；不借旧成员身份授权。
- 工具准备和聊天参考构造有独立准备阶段；不更改模型超时、Focus策略或压缩失败策略。
- 来源清单当批增加`src/maisaka/focus/group_runtime.py`后为28源＋锁29项；当前总数见顶部来源登记，实际部署以实例发布记录为准。

#### 有限只读恢复（2026-11-06）

- 2026-11-10同版修复：只读恢复模板在构造历史Item与运行Hook前复用N04的宿主临时控制过滤。正常Planner仍保主动任务，账本不删；避免合法请求级控制去重被误判为历史丢失。真实IM（包括引用控制语法）、来源、工具配对和保留条目的完整身份/内容检查不放宽。合成回归覆盖检查点恢复、跨来源改写拒绝及输入水位保持；不依赖私有插件源码或生产聊天。

- 首次请求、失败撤销执行快照及检查点恢复后，复用真实请求准备链取得只读压缩模板；模板不成为可执行快照、不落盘，失败后按当前成员与配置重建。
- 参考准备后接入的就绪消息也检查压力。沿既有`max_context_size`规划完整条目，单次至多8批，顺序合并前序摘要；每批不超过既有消息条数，近期原文保留。没有新增Token计量、容量配置或模型预算；超长单条、不可拆批次或必要模板仍可能失败。
- 以构造链记录的历史Item身份和内容证明归属，再移除全量旧历史；Hook新增参考保留，Hook删除/改写历史或未知工具协议则拒绝替换。摘要只携带当前批次、前序摘要与只读模板；外接Planner未提供此模板时保留输入并失败，不改走其它模型。
- 全批成功且generation、成员有效性、配置、条目投影及输入水位仍一致，才一次替换；失败、取消、变更或批次耗尽保留原输入，不提前推进`_considered`，按原N11等待新输入。摘要中工具调用只作为资料，不执行或重放unknown。
- 原有成功快照的常规压缩失败丢旧原文合同保持，新增恢复入口独立；不改共享范围、wait/主动任务、普通steering、模型人设或权限。合成容量拒绝器只证明机制，不代表生产Token上限或真实模型/QQ送达。

### 私聊自身回传与相邻归属修复（2026-11-04）

该节记录11月4日首次配套修复，后续已由当前维护线继承；旧版本号/来源数量不作为当前配置。
当批适配器基于官方v1.1.13/9b409b192：保留发送者，透传自身私聊回传的真实target_id，
缺接收者拒绝而不创建“与机器人自己私聊”；显式接收message_sent，保ignore_self_message和黑名单。
该补修必须与宿主同行为版本一起发布，单换适配器不能修旧宿主按发送者计算会话的逻辑。

宿主统一私聊对端解析用于预处理Hook前的session、注册/创建、入站名单策略和文件来源；
自身回传不覆盖对端昵称/群名片，辅助route_metadata不能改写已验收目标。
入站去重键补齐平台/账号/scope/群私目标/发送者，避免同驱动跨会话同ID互相吞消息。
私聊WebUI沿完整会话/发送者/ID配对发送和回传；模型上下文按同样正式身份保单份自身消息，
处理发送先到、回传先到及媒体await期间发送完成；重启恢复也只投影一份，不删除数据库记录。
忽略自身消息关闭时，同账号其它客户端手动发信仍保留并标为自身；开启时继续按用户选择过滤。
不按正文去重、不清库或迁旧错归属记录；配置、人设、权限、忽略开关和协议端保持。
此前“仅保存源码”已由后续授权配套发布接续，当批登记28项；当前适配器版本与Host来源集合只查顶部状态，
不据这段历史重新安装旧适配器或重放配套事务。

1. **精确发送回执**：只读 `maisaka.reply.after_send`，携带真实 session/reply/call ID、
   逐段发送结果及实际文字；Hook 失败不改投递结果，不补发，不把 unknown 当成功。
2. **表达选择整体 20 秒协作期限**：超时当轮跳过表达注入继续正文；保留外部取消语义，
   不承诺上游已接请求能硬取消或退费，不改学习和人设。
3. **Life 新摘要输入隔离**：过滤有归属的临时素材和内部任务，真实聊天、引用、已发言、
   工具调用配对及已有摘要保留；不清空历史，不追溯抹掉旧摘要。
4. **可复现依赖锁**：1.3.15阶段修正Dashboard开发版锁；1.3.16按用户选择SDK2.17。
    正式1.3.19明确要求SDK **>=2.20.0**，定向固定 **2.20.0**，
    仅定向升级Dashboard **1.9.13**和宿主 **1.3.19**。上游不再提供锁，fork保留`uv.lock`；其余包版本沿用最后官方1.3.16锁，
   `lock_baseline_commit`登记该正式祖先，门禁比较完整包集合且核对新tag依赖声明，不放开通配更新。
5. **显式opt-in可信工具上下文**：仅为工具metadata声明`trusted_context="neko.tools.context.v1"`
   的插件工具构造并覆盖`_host_context`；非opt-in工具保持原行为。
    身份来自实际执行上下文，Focus不能借别群锚点账号，无有效上下文时不给模型信封背书。
     当前用于独立的Neko Files，消费者仍须核实会话/账号和自身授权；不是新增通用放行权限。
6. **uv错误格式兼容**：1.3.19新增自更新的故障源识别同时接受旧反引号URL和uv0.13的无反引号URL，
   保留严格索引路径归属、非网络错误不重试、显式源绑定与原回滚行为；不改变固定提交部署路线。
7. **Gemini失败元数据**：复用失败快照/尝试事件和日志，仅记录结束/拦截枚举、候选/片段/Token数量、
   输入条目/图片/工具数量及原耗时；不增加正文、签名、鉴权头或原始响应落盘。
   缺少响应元数据时明确未知，不把超时猜成网络断连或安全拦截；不改请求、重试、路由、超时或取消。
   失败快照上限和事件存储沿原实现，无额外模型调用或重放，诊断自身出错仍保留原异常和快照。
8. **插件RPC单帧32 MiB**：Host与Runner使用同一Connection上限，容纳带图片的较大Planner Hook上下文；
   超限仍在发送前或接收长度头时拒绝，不改分帧协议/传输监听/插件权限，不设无限载荷。
9. **私聊内部历史压力回收**：真实IM条数窗口保持原配置；另对闭合工具批次的内部Item设置
   192触发/96目标滞回，保所有真实原文、引用、pending/active/latest及媒体锚点和未知provider依赖。
   沿原Planner生成单份有效滚动摘要后才提交；失败/取消/并发变化保原历史，群Focus不接入，
   不清数据库/媒体，不把Item数量当模型Token或RPC字节预算。保护依赖过多时明确不可回收。
10. **Hook媒体binary运输与去重**：已协商的Host/Runner请求前Hook按每帧媒体表表示规范图片，
    每个唯一Base64只校验/解码/摘要一次，重复位置共享binary；插件/模型仍见完整原字段、图字节与位置，非规范Base64原样保留。
    hello旧端无能力时走legacy，wire及还原后的普通Envelope仍限32MiB；坏帧拒绝不重放，
    无跨轮缓存/磁盘引用服务，不宣称减少Gemini输入或费用。全图转换的CPU/内存代价已实测披露，
    后续用户明确接受资源权衡并要求启用；sidecar不比ordinary小时仍走legacy，不借运输压缩扩大恢复上限。
11. **表情GIF差异计算修复**：`ImageUtils.gif_2_static_image`在差值与平方前转为float32，
    修复uint8溢出导致默认MSE阈值下仅保留首帧；同时严格遵守`max_frames=1`并拒绝非正上限。
    保留与上一选中帧比较、差异严格大于阈值、默认1000/最多15帧、200高横拼JPEG及现有调用链；
     不换GIF插件、不改模型/其他视觉路径，不重建生产描述缓存。11月2日后续明确授权后已同版上线，见发布记录。
12. **纯语音回复不附加引用**（2026-11-04维护实例已上线59ae04f9b）：回复扩展结果经宿主最终逐段发送时，
    纯`VoiceComponent`分段关闭引用，包括没有文字时被当作首段的`voice_only`；`quote_previous=False`
    本身只约束后续引用，不能阻止原首段抽样/补充器引用。保留文字与混合消息的原引用规则、回退及回执。
    公共固定TTS消费者回归覆盖两条引用路径下的纯语音、文字加语音、原文字回退，真实执行发送前引用插入
    与序列化并核对VoiceVisibility识别；网络/实际投递替身，不调用TTS或QQ。当批十九源＋锁二十项已由当前登记继承。
13. **WebUI群聊自身回传显示合并**（2026-11-04用户随后授权，维护实例已上线）：
    `dashboard/src/routes/monitor/maisaka-monitor.tsx`在气泡投影时，以完整QQ平台/正式会话/群/发送者/消息ID，
    将已加载的`message.ingested`自身回传与`message.sent`配对，只显示原发送事件的正文/引用/媒体。
    两遍扫描不依赖到达顺序，实时、重连与历史翻页共用；缺身份、不同ID/账号/群/会话、无对应出站的自身消息保留。
    不按正文去重，不改事件账本、分页游标、消息数据库、入站或模型上下文；未加载的另一半不提前猜测隐藏。
    群聊页实际使用观察时间线，不是本地聊天室`ChatHistoryManager`。维护实例已使用同提交Dashboard1.9.13
    本地构建（Bun1.2.1冻结锁、tsc/Vite通过），开启原生MAIBOT_WEBUI_USE_LOCAL_DASHBOARD入口，
    221静态文件校验及首页/主JS/CSS/监控chunk在线hash通过；原安装包/依赖锁保持。未运行额外前端测试；用户随后反馈“看起来都没问题了”，按用户实际反馈收尾。
    后续宿主升级必须同步核对并准备匹配的本地Dashboard构建，不能只升级PyPI包却仍服务旧dist；上游采纳后可撤维护入口。
14. **Gemini非流式候选边界**（2026-11-04维护实例已上线e1a5e776e）：默认非流式解析按SDK的`response.text`
    语义只取第一个候选，正文/思考/工具选择一致；多候选不是连续分片，不再拼成重复正文或执行备选工具。
    保留同一候选内全部文本分片、原始响应/计费统计、空回复处理及原内容拦截止损；不做文本对半删重。
    仅非流式`maisaka.replyer`出现多候选或整段精确两遍时，日志记录request_id、候选/片段数量、前16片长度、
    解析与首候选相等关系；不记正文/签名/参数/文本hash、不增加请求。诊断失败保留原结果。
    两个同文候选的双份结果已用真实SDK离线复现并修正；现场成功快照没有原始候选结构，
    尚不能确认这就是用户所报重复的根因。现已上线且用户反馈正常，本轮交付结束；若以后复现才查对应自然诊断，不主动开启忽略/重放收费请求补证明。
15. **明确内容拦截止损及任务内定向备用**（原止损9441aa20a，2026-11-05/11扩展）：仅Gemini结构化
    `prompt_feedback.block_reason == "PROHIBITED_CONTENT"`触发独立ContentBlockedError；原模型不重试或惩罚可用性。
    未锁定单一模型且尚未交出流式块时，legacy_grok按任务列表顺序尝试未失败的候选，实际API
    `model_identifier`必须精确为`grok-4.5`、`grok-4.6`或`grok-4.7`，不按显示名或子串猜测。
    custom仅使用任务内指定有效生成模型名称，disabled不定向回落；非custom目标残留运行忽略、保存清空。
    进入此路径后只走所选交集，不新增候选；备用失败保原有限重试，不再回其它模型，无可用候选则结束。
    已交出任何流式块（包括元数据）后再拦截则直接结束，避免自定义解析器副作用或两模型输出混合。
    保原上下文工厂按目标客户端重建、失败快照/终态及有界元数据，不删图替词；配置字段与保存合同见顶部。
    其它空响应／网络／超时／取消保持原行为。实际部署以实例来源登记为准。

[neko-patches.json](neko-patches.json) 是重新构造的公开差异清单，只有上游标识、包版本及源码哈希，
不包含任何实例登记、运行路径、账号、Key 或配置。1.3.15的初始字节审计留在历史；
1.3.16保留原维护行为并合入上游对相同文件的变更；1.3.17上游未改这五个源文件。
1.3.18仅在chat_loop_service/generator两文件同步群/私注意力漂移调用，其余三源字节不变；
五项维护行为均保留。1.3.19上游未改这五源，字节全部保持；原产品源码未整体格式化，锁保留LF字节。
1.3.19初次集成时修正uv错误解析，登记为六源＋锁七项；后续同版Gemini失败诊断/RPC32
补修当时扩为九源＋锁十项；后续内部历史压力与重复媒体运输扩为十五源＋锁十六项；
后续GIF修复扩为十六源＋锁十七项，内容止损扩为十九源＋锁二十项；本次私聊链路补修
当批扩为**二十七源＋锁二十八项**，后续N11/N12扩为29源＋锁30项，N15当时扩为30源＋锁31项。上述均为历史演进，当前集合只读清单。运行固定提交与发布验收以实例登记为准，
文档提交前移不构成待补部署。
此前1.3.18/f636d1092、SDK2.19.0/Dashboard1.9.12，以及1.3.17/789bac67e、
1.3.16/e7cc25bdf的线上记录留作历史，不覆盖顶部当前1.4.3/SDK2.22.1状态。
不按旧hash重套补丁，也不把旧文档“未部署身份桥/未上线1.3.16”当现行状态。

### 文件能力的选择

- 继续使用独立 **[Neko Files](https://github.com/RoselindLabs/neko-files)**；已解除原公开分发hold并按MIT独立维护，
  具体版本、运行SHA及名单归插件运维记录，本仓不复制第二张插件版本表，也不把解析器或运行数据放进GPL宿主仓。
- 官方 `capabilities.files` 默认false保持，不自动开第二套索引/下载/文件工具；Neko Files独立名单、
   群目录/OCR开关与现有`neko.tools.context.v1`身份桥保持，不改用`ctx.files`；SDK更新
  是宿主依赖升级，不代表插件切换取源、开放新能力或修改其最低兼容声明。
- 上游的`_file_scope`与本fork的`_host_context`并存；官方文件关闭时仍须允许Files旧链路执行，
  官方文件开启时其临时凭证不应覆盖本fork身份信封，也不能借模型参数选择其他聊天。
- 官方PDF/Office目前只存储收发，不解析；Files仍负责文档/单个压缩包成员读取。
  后续是否复用官方附件底座需另行适配、核验SDK/QQ适配器，并保留插件来源许可审查，非本次任务。
- Files四工具及单份Store/Cache、同聊天ID／原始file段／一跳引用／排队撤权保持，
  后续稳定ID刷新、未知busid省略、URL-only不续旧链接、永久OCR准入及范围续读由插件自身维护。
  已有隔离Runner/实际加载证据，但不等于真实QQ附件/私聊/OCR全格式验收；
  最低Host/SDK声明以该插件当前manifest为准，不沿用旧版本上限、不通过改声明掩盖兼容问题。
- 文件形式JPG/PNG尚无插件视觉入口；旧NapCat取址失败属于迁移前证据，后续已切SnowLuma，
  不据单个文件的成功推断全格式／私聊／OCR均可用。额外图片/视频附件适配已暂停。
   当前官方Files可管理/下载/收发附件及读取基础文本，开启不等于自动看图或视频分析，
   也不能保证修复QQ取址。本轮不开官方Files、不迁移协议端；已有文档能力和授权保持。

<a id="模型请求超时在哪里设置"></a>
## 历史模型请求超时入口（1.9.13）

以下入口按历史 **Dashboard 1.9.13** 源码核对，不能直接当作1.4.3 Host下的访问路径或配置文件位置；
当前提供商/模型定义在`host/model_catalog.toml`，实例任务配置仍在`config/model_config.toml`。
当前Host路由合同见顶部；本次没有重新逐项验收新版模型管理UI。历史界面显示的是中文字段名，不是配置键`timeout`：

<details>
<summary>展开旧界面路径与当时字段说明（不是新版UI验收）</summary>

1. 打开模型管理页的 **模型设置** 标签；也可在现有WebUI地址前缀后访问
   `/config/model?tab=configuration`。
2. 在 **模型厂商** 中选中目标模型实际引用的提供商。桌面端为左侧列表，手机端为下拉框；
   选择“全部”不会显示某个厂商的编辑入口。提供商名称由实例自行命名，不存在固定叫“自己”的系统菜单。
3. 点击选中厂商信息栏右侧的 **铅笔图标（编辑厂商）**，不是编辑下方单个模型。
4. 在弹窗里向下找到并展开 **高级配置**（每次打开默认折叠），其中的 **超时(秒)**
   就是提供商单次API请求超时。它与“最大重试”“重试间隔(秒)”位于同一区域。

对应配置为`config/model_config.toml`里目标`[[api_providers]]`条目的`timeout`，
通过模型条目的`api_provider`匹配提供商`name`，不要新增同名提供商或在模型额外参数里另填`timeout`。
该设置影响使用同一提供商的模型，不只某个Gemini模型；`client_type=gemini`时宿主将秒换算为毫秒交给Gemini SDK。
这里只说明字段位置，不代改实例数值。界面帮助里的“默认30秒”与当前后端默认120秒不一致，
实际以已保存配置为准，不把占位提示当当前值。

**另一个超时不是同一层**：`model_task_config.planner.hard_timeout`或
`model_task_config.replyer.hard_timeout`位于 **功能分配** → 打开功能任务栏的 **高级** 开关
→ 选对应任务 → **时间上限 (秒)**，
限制该任务对**当前一个候选模型的整段尝试（含重试/间隔）**；超时后可按原调度切下一个模型，
不是所有候选模型加总的统一总预算。因此即使提高提供商`timeout`，较短的任务硬超时仍可能先触发。
也不要与本fork的表达选择整体20秒期限混为一谈。

入口依据：`dashboard/src/routes/config/model.tsx`、
`dashboard/src/routes/config/modelProvider/{ProviderSidebar,ProviderForm}.tsx`；
执行语义依据：`src/config/model_configs.py`、`src/llm_models/utils_model.py`及
`src/llm_models/model_client/gemini_client.py`。后续界面改版时应重新核对，不沿用旧“API提供商→timeout”简称。

</details>

## 本地验证与 CI

克隆时使用 `git -c core.autocrlf=false clone ...` 保留Git对象字节；已有带改动目录不要批量覆盖重检出。
CI也显式禁用检出换行转换，源码/许可证哈希仍按字节严格核验，不靠放宽CRLF断言通过。

```console
uv sync --locked --group dev
uv run --no-sync python scripts/neko_check.py
uv run --no-sync python scripts/neko_source.py --output <checkout之外的新路径>/maibot-neko-source.zip
```

运行公共契约前须在仓库外准备 `neko-tts` 源码克隆，将环境变量`NEKO_PLUGIN_FIXTURES`
指向其父目录。固定TTS提交 `7be09dec956fbae6a8600eef5df14cf116e18d57`；逐个核对消费文件与
固定提交字节，缺失或变化直接失败、不静默skip。CI只checkout公共TTS，不把插件源码打入宿主包。

Life已退役且仓库改为私有，2026-10-28用户批准解除公共CI的私仓依赖；不增加跨仓secret、不公开源码。
原Life真实集成测试完整保留在`pytests/neko_retired/test_life_bridge.py`；本地显式运行
`uv run --no-sync python scripts/neko_check.py --include-retired-life`时额外要求Life固定提交
`f6116cfc76943c9bd6b78b47d309478645908a75`，缺少源码仍失败，不自动skip。
公共CI不冒称执行私有Life消费者验证；宿主摘要隔离/精确回执/共享Focus保护与实际TTS消费者仍为必跑项。

- Linux 生产锚点 Python **3.14.4**，Windows 最低 Python **3.12**；
  Dashboard **1.10.3** 从同一校验锁安装，SDK固定 **2.22.1**；CI使用uv0.13.5，不把 latest 当生产环境。
- 运行真实宿主类、真实 SDK，以及回执/取消/摘要/共享 Focus/回复扩展的受影响合成契约，
  不重复七插件全库，不声称覆盖整个上游。测试使用临时实例，禁止真实模型或 QQ 请求；
  唯一网络例外是 Windows 标准库内部创建 asyncio socket pair 的自连接，不放行模型代理或适配器端口。
- 关键组必须非空、每个发现用例实际通过，任何意外 skip/未执行都失败；不靠固定总数守门。
- GIF回归实际调用宿主helper，纯内存合成黑/白/红帧并解码输出JPEG，检查升降亮度、顺序、
  相似帧相对上一选中帧过滤、阈值等值跳过、单帧和`max_frames`上限/非正拒绝；纳入必跑关键组。
- 上游1.3.16删除了维护门禁依赖的`test_context_compaction.py`/`test_planner_text_reply.py`，
  fork保留其回归。旧`set_reply_options`被上游移除，相关断言按新补充编排改为验证同一请求快照、
  自然语言指导、缺快照失败及Focus原reply路径；不恢复废弃产品API或静默删门禁组。
  新增图片在正文前的精确回执、原生文件凭证与旧身份桥共存/取消撤销合成覆盖。
- 1.3.17额外定向覆盖模板/旧自定义模板兼容、SDK运行时控件的操作白名单及认证、内部聊天身份，
  使用合成配置验证HTTP字段逐项迁移/已有值优先/幂等、不改变模型人设/能力开关。
  官方文件、桌宠和新增跨聊天流工具仍默认关闭；不改用户Focus、任务超时或调用模型/QQ。
  模板仅纳入本tag新增的4项回归与三语八模板加载/元数据检查，不宣称整个上游模板套件通过：
  探索性全文件运行有3项失败，2项受门禁固定`MAIBOT_LOCALE=zh-CN`影响，另1项为未改动的
   `maisaka_expression_selector`既有元数据缺失；不为扩大本次小更新范围而修改产品模板或全局locale策略。
- 1.3.18只追加本次群/私漂移、HTTP_HOST/旧WEBUI_HOST优先级、配置副本拆分、监控思考和
  适配器运行时协议/路由身份回归，不重跑全插件或修改SDK。遥测增加platform/protocol且聚合移出主循环，
  不变更原遥测开关；合成检查不含账号/scope等身份载荷，不连接遥测或QQ服务器。
  原路由门禁纳入正式身份/跨路由拒绝/RPC三项；本次私聊补修另检查完整归属用例，
  将过时的“下个版本移除”日志断言改为实际旧归属迁移提示，未改变产品兼容行为。
- 源码包只来自干净确切 Git SHA，验证运行文件成员、清单全部哈希及临时解压目录真实 SDK 导入，
  检查模块来源及三语八种迁移模板，禁止回退可编辑安装源码。ZIP 和附带 JSON 含确切 commit/SHA256，
  CI artifact 保留 7 天。
  artifact 不是公开 Release，也不是生产发布证明；本仓门禁不冒充七插件上传器门禁。

1.3.19追加本tag的人设持久化/权限拒绝、桌宠按钮归属/卸载/重复动作拒绝、上下文准备状态、
工具错误监控、插件市场及依赖源修复回归。原裁切边界测试适配正式tag的100%触发阈值，
20%保留目标、五条最低保留、真实聊天计数、工具配对及Life摘要过滤保护不变。
完整旧配置副本迁移只允许版本号变化与新增空`personality.profiles`/`selected_profile`，
显式上下文条数、人设、群/私提示词、Focus、注意力漂移及禁用能力保持；不切换人设或调用新能力。

**文档与小改动**：纯文档只检查受影响的链接/锚点、台账结构与diff，不重新同步依赖或运行整套契约/构建，
也不因文档HEAD前移补部署。业务小改先做最小相关检查，复用已有证据；确需发布时仍按同SHA门禁与独立前端制品合同执行，
不为凑测试数量新增重复用例或追加全插件矩阵。

## 分支与上游同步

- 维护仓现归属[RoselindLabs/MaiBot](https://github.com/RoselindLabs/MaiBot)，2026-11-04从个人账号原位转移，
  历史提交、上游fork关系、维护分支及固定部署修订保持。旧日期CI链接保留其证据语义；新CI限定组织仓，
  TTS固定fixture仅更新owner、SHA不变。组织迁移本身不触发生产重启或部署。
- `origin` 为此 fork，`upstream` 为官方仓库；`main` 仅作上游快进镜像，**`neko/main` 为维护默认分支**。
- 每次只选择正式 tag，先 fetch/审查变动，在临时集成分支合并到维护线；逐项判断现有差异是否
  已被上游实现、仍需保留或需要移植，再验证相关行为。不要自动追 main/dev，不强推共享历史。
- GitHub Sync fork 只用于镜像线；不能对维护线使用丢弃提交式同步。
- 上游 16 个 workflow 原样职责留在 `.github/upstream-workflows/`，不在活动 workflow 路径。
  创建后先关闭 Actions；注册过的继承 workflow 逐个禁用，未注册的也不启用，再开放唯一只读 `neko-ci.yml`。
  因镜像线仍含官方 workflow，**每次推镜像前都要先关闭仓库 Actions，检查新增 workflow 并禁用**；
  不能只看维护分支安全就开放镜像流程。仓库默认 token 只读且不能批准 PR，无生产 secrets/OIDC。
- 不向 PyPI、Docker、CNB、Crowdin 自动发布，不自动改依赖、开上游 PR 或部署。
  未来官方新增/更名 workflow 必须先审查，不能假定旧禁用列表永久覆盖。

## 发布与迁移边界

**现行顺序**：先获得维护/重启授权，在线核对实例来源、配置和目标差异，准备已验固定fork SHA及匹配Dashboard；
说明窗口后一次停写备份→切换→最小就绪验收。保留旧源码、登记、Host状态、旧环境/锁/dist及匹配运行数据，
不在停服后下载/解依赖、不生产pull/merge、不重跑旧overlay升级器，不让旧源码直接读已迁移数据。
回退须保后续unknown和外部动作现场，不能只退Git或覆盖旧配置/账本。

**源码交付不等于部署授权**：文档HEAD、CI artifact或克隆仓库都不是新生产发布；不为补文档重启，
不据旧快照换模型、人设、权限、等待时间或试发QQ。运行venv仍引用的暂存目录不可按旧清理授权删除，
实际路径及匹配备份由实例登记管理。第三方插件继续独立维护，热更不代替宿主授权，也不重启宿主代替插件加载。

公开仓不收录真实配置、Key、账号/群名单、聊天/摘要/DB、私有Prompt、人设、插件运行数据、`.venv`或私有来源登记。
独立Files现状见[文件能力](#文件能力的选择)；原fast_model任务超时与宿主整体20秒保护不是同一层，升级不随意移除。

<details>
<summary>历史：11月4日配套发布及1.3.16～1.3.19迁移（不重放）</summary>

**历史发布快照（2026-11-04，非当前运行状态）**：维护实例1.3.19，当批固定`1f1c41d0e0dcd00a4352ccdcb6c2c2c9c2a004a5`，
SDK2.20.0／同提交Dashboard1.9.13／DB46／28源锁登记。配套适配器维护SHA为`b44d874`，
先经标准上传器热加载，再切Host和前端；保持原1.1.13版本、插件ID、目录与配置。

[Host同SHA CI41845203496](https://github.com/znzsofficial/MaiBot/actions/runs/41845203496)双平台契约/源码包通过，
[适配器CI41847257068](https://github.com/znzsofficial/maibot-snowluma-adapter/actions/runs/41847257068)
双平台58项／真实SDK隔离包／两门禁通过；服务器23项私聊回归、配置副本与全部来源hash验证通过。
Dashboard以Bun1.2.1冻结构建，221资源及首页/主JS/CSS/监控chunk在线hash一致。
一次停写匹配备份后62.81秒就绪，11启用插件/适配器恢复；模型、人设、超时、权限、
依赖与其它业务服务保持，没有人工模型/QQ探针。忽略自身消息保持发布前开启值，旧消息库不搬迁/删除。

上一版e1a5e776e的522项／31项／61.19秒、9441aa20a的24项／66.02秒和9e1c的GIF验收
均是历史证据，不充当本次验证。克隆仓库不等于部署；源码/文档HEAD前移不触发补部署或重启。
不收录真实配置、Key、群名单、聊天/摘要/DB、
私有 Prompt、人设、插件运行目录、`.venv` 或私有补丁登记；第三方插件继续独立维护。

1.3.16的数据库schema46及`http.host/port`迁移已在上一轮线上完成；1.3.17未新增DB schema，
配置8.15.59→8.15.62、模型配置仍1.17.25。其修复补迁移旧`webui.host/port`且已有HTTP字段优先；
正式切换前仍须用最新配置副本验证并保留原监听/端口，不能套默认值覆盖入口。
上游模板迁移到统一`prompts/<locale>/`，过期自定义模板会保留原文件并改用当前内置模板；
切换前须核对实际自定义模板兼容，不改用户人设或私有Prompt来消除提示。
Files源码/配置不纳入宿主仓；当前独立发布情况见上文，不用旧1.3.17/2.19兼容记录替代现行验收。
原fast_model任务超时与宿主整体20秒保护范围不同，不随本次合并改用户模型或移除保护。

### 1.3.18的必要配置副本迁移

数据库仍schema46、模型配置仍1.17.25，主配置8.15.62→8.15.63。上游删除旧
`experimental.attention_drift`，但没有对应UpgradeHook；直接启动会丢弃旧表并给群/私两表填默认值。
切换前必须先对最新副本执行一次下列工具，再由新宿主在隔离实例完成正常版本迁移：

```console
uv run --no-sync python scripts/neko_prepare_1318.py --input <private-copy>/bot-original.toml --output <private-copy>/bot-prepared.toml
```

工具不运行宿主、不改输入，输出必须是尚不存在的新路径；两表各继承旧完整设置，显式已有的新字段优先。
不新增产品UpgradeHook或改legacy_migration，不默默开/关漂移。对比迁移后只允许版本号和这次表拆分变化；
其余配置、模型任务、人设、共享Focus/私聊排除、插件开关/名单、TTS与自定义Prompt保持。
正式切换须用此预期副本并重新校验原配置hash；旧1317事务已完成，不可原样重放。

### 1.3.19配置与发布（已完成）

主配置8.15.63→8.15.68、模型配置仍1.17.25、数据库仍schema46；没有新增配置UpgradeHook。
现有显式配置值应全部保留，人设新增空备选库和空选择名，不自动导入、改写或切换当前人设。
本tag群/私上下文默认条数从30/60改为50/75，但不得覆盖已有条数；裁切触发由120%降为100%，
这是保留原条数时也会发生的上游行为变化，可能更早摘要，不能承诺token或模型费用不变。
桌宠仍默认关闭、官方Files与Neko Web选择不变。SDK新增personality能力仍按manifest独立授权，
本次不为任何插件增加权限。EULA/PRIVACY由上游改为使用须知，启动不再交互确认；保留旧确认文件审计，
不伪造确认、不调整服务环境。自更新源修复不改变本fork按固定提交部署、不用原生自更新的选择。

1.3.19初始升级及后续同版修复均已分别获授权完成；最新配置/模型/自定义Prompt副本与SDK2.20环境已验，
Files另有真实隔离Runner及热加载验收。不要为补齐文档重新部署、重放已完成事务或只改旧执行器SHA白名单。
未来新发布仍须另获授权，先在线准备并核验最新副本/锁定环境，再停写备份与切换；本地合成配置不替代实例副本验证。

</details>
