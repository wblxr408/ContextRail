# Proposal

## Why

当前只能手工保存单次结果，源码目录没有可继续的范围导入，文件编辑/删除也未映射到当前证据。C01/C02接通显式目录读取、分批续读及版本失效，使现有FTS索引能用于实际源码目录。

## What Changes

- 项目目录配置支持include/exclude、原生gitignore、深度/文件数/单文件体量限制，跳过依赖/生成目录与越界链接。
- 持久化明确run游标与来源映射，分页继续不猜另一任务；文件修改产生新revision，相同内容不重复写，删除仅在完整扫描后失效。
- 导入状态与条目可分页读取，触顶/未覆盖/出错可见并给出继续入口。
- 可选显式入口将已保存的文档版本送入原工程记忆jobs队列，复用原clean/enqueue，不启动服务、不自动把源码全量转知识。

## Capabilities

### New Capabilities
- `bounded-directory-sources`: 有范围和预算的目录导入、持久续读、文件版本与删除协调及显式记忆队列连接。

### Modified Capabilities
无。

## Impact

Store schema8新增来源/run/条目元数据；沿用现有项目Scope、owner租约、原文Store与FTS。新增可信CLI写入口和只读状态工具，原记忆服务源码不复制、不改后台。
