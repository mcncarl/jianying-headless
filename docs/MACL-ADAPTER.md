# com.apple.macl 权限保留适配器（clonefile 临时索引）

关联 [Issue #5](https://github.com/mcncarl/jianying-headless/issues/5)。本文件说明
`publish()` 临时索引准备的权限保留机制、探针工具用法与已验/未验边界。
判定规则本身没有放宽；本文不描述也不支持任何忽略 `com.apple.macl`、
按长度放行、删除属性或关闭 SIP/TCC 的方案。

## 背景

在受 TCC 保护的草稿根目录里，内核会给**直接新建**的文件追加
`com.apple.macl`（长度随授权名单变化），该属性 SIP 保护，用户态不能写也不能删。
原首页索引由剪映自己创建，不携带它；因此临时索引无论如何复制属性都无法与
原索引逐属性一致，旧的 `copy_xattrs` 严格判定会拒绝（这是正确的失败方向）。

## 机制

`publish()` 准备临时索引时（`engine/jy14_headless.py` 的 `publish`）：

1. 用 `clonefile(2)` 把原首页索引克隆为临时文件（APFS 写时复制克隆保留
   源 inode 的属性集合）；随后对克隆体原地 `O_TRUNC` 覆写为新索引内容。
2. 仅在克隆**明确不受支持**（`errno.ENOTSUP`，例如非 APFS 卷）时回退到
   原先的直接新建路径，过同一道判定门；其余错误（权限、目标已存在、
   符号链接等）不静默回退，直接失败。
3. 覆写后照常执行 `copy_xattrs`：仍只容忍 `com.apple.provenance` 值变化
   且两侧均存在；`com.apple.macl`、`com.apple.quarantine` 及任何属性
   出现/消失仍拒绝。提交前复查与失败恢复逻辑不变。
4. 审计 `prepared.json` 记录 `prepared_via`（`clonefile` 或 `create`），
   不伪报完整保留。

剪映可读性是构造性的：最终索引 inode 的属性集合与原索引同构（无 macl、
quarantine 相同、provenance 均在），不依赖对 macl 语义的假设。

## 探针工具

`tools/macl_clone_probe.py` 用于在某台机器上复核上述内核行为，输出只含
属性名称、字节长度、存在性与相等布尔的 JSON：

```bash
python3 tools/macl_clone_probe.py
```

- P1：草稿根直落新建文件（受影响机器预期：新增 `com.apple.macl`）。
  不符合预期时探针停止——说明环境与已验案例不同，需先重新定性。
- P2：clonefile 原索引到草稿根新文件名，覆写少量字节前后各查一次。
- P3：新建子目录内建文件，再 `os.replace` 到草稿根，查文件与子目录。
- P4：仅当 P2、P3 结果文件都带 macl 时，用 `copyfile(3)` 对照。

边界：只新建带随机后缀的隐藏夹具（0600/0700、`O_EXCL`），原首页索引仅作
clonefile 只读源；只读查询扩展属性；不写/删属性，不联网，不用 sudo；
出现 EPERM/ELOOP/目录形态异常即停止对应分支。夹具不自动删除：
路径已在输出的 `fixtures_created` 中列出，清理时只把这些确切路径移入
垃圾篓，不用通配或递归删除。原始属性值只在进程内存中比较，不进入输出。

## 已验 / 未验

已验（单机 macOS 27.0 arm64、剪映 11.5.0、基于 344a78f 的候选）：
探针 P1–P3 如上预期（P4 按条件跳过）；51 项全量单测与 pin/包检查通过；
两条真实草稿（其一为 41 段分段音频时间线）完成 build → publish →
剪映打开/播放/编辑/保存/完全退出/冷重开 → 回读，最终索引属性
`{provenance, quarantine}` 无 macl，既有首页条目逐一不变。

未验：其他机器与其他 macOS 版本；非 APFS 回退路径的真实环境（仅 mock 覆盖）；
克隆在维护端环境的探针结果。验收在日常首页（有备份与回退约定）完成，
不等同于隔离测试账户环境。
