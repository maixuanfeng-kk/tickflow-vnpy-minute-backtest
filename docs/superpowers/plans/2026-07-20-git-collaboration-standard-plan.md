# Git 协作与交付规范 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `.codex` 中交付一份禁止 rebase、强调语义化冲突解决的 Git 协作与交付规范。

**Architecture:** 使用单一 Markdown 文档作为团队唯一操作依据。文档按工作生命周期组织，并在每一阶段提供命令、判定标准、例外审批要求和交付模板。

**Tech Stack:** Git、PowerShell、Markdown。

---

### Task 1: 编写团队规范

**Files:**
- Create: `.codex/GIT_COLLABORATION_AND_DELIVERY_STANDARD.md`
- Reference: `docs/superpowers/specs/2026-07-20-git-collaboration-standard-design.md`

- [ ] **Step 1: 建立文档的规则骨架**

写入适用范围、角色、分支规则和不可违反的约束：禁止 `git rebase`、`git pull --rebase`、强制推送和通过 `reset --hard` 清理共享历史。

- [ ] **Step 2: 写入开发、同步与冲突流程**

用 `git fetch origin` 后 `git merge origin/main` 作为唯一主干同步方式。对每种冲突要求记录双方意图、最终保留的行为和验证结果，并规定默认同时保留旧功能与新功能。

- [ ] **Step 3: 写入提交、推送、合并、交付和回滚流程**

规定只暂存任务文件、提交前差异检查、负责人执行 `git merge --no-ff <feature-branch>`、推送前验证和使用 `git revert` 回滚。加入逐项检查清单与交付说明模板。

- [ ] **Step 4: 检查文档完整性**

Run:

```powershell
rg -n "git rebase|git pull --rebase|<<<<<<<|TODO|TBD" .codex/GIT_COLLABORATION_AND_DELIVERY_STANDARD.md
```

Expected: 前两项仅出现在“明确禁止”段落；没有冲突标记、`TODO` 或 `TBD`。

- [ ] **Step 5: 检查待提交范围**

Run:

```powershell
git status --short -- .codex docs/superpowers/specs/2026-07-20-git-collaboration-standard-design.md docs/superpowers/plans/2026-07-20-git-collaboration-standard-plan.md
```

Expected: 只显示本任务新增的规范、设计和计划文档；不暂存或修改其他工作区文件。
