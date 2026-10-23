#!/usr/bin/env bash
# export-session.sh — 导出 Claude Code / Codex 会话上下文为 Markdown（限流可用，纯本地）
set -uo pipefail   # 去掉 -e，避免 find 无结果时整脚本中断

command -v jq >/dev/null || { echo "需要 jq，请先安装"; exit 1; }

# 递归找最新 jsonl：参数为若干根目录，用 find 按修改时间排序取第一个
latest_in() {
  local d found="" ts newest=0
  for d in "$@"; do
    [[ -d "$d" ]] || continue
    while IFS= read -r f; do
      ts=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null || echo 0)
      if (( ts > newest )); then newest=$ts; found="$f"; fi
    done < <(find "$d" -type f -name '*.jsonl' 2>/dev/null)
  done
  [[ -n "$found" ]] && echo "$found"
}

TOOL="${1:-auto}"
SESSION=""
case "$TOOL" in
  claude) SESSION=$(latest_in "$HOME/.claude/projects") ;;
  codex)  SESSION=$(latest_in "$HOME/.codex/sessions") ;;
  auto)
    C=$(latest_in "$HOME/.claude/projects")
    X=$(latest_in "$HOME/.codex/sessions")
    if [[ -n "$C" && ( -z "$X" || "$C" -nt "$X" ) ]]; then SESSION="$C"; else SESSION="$X"; fi
    ;;
  *) SESSION="$TOOL" ;;
esac

if [[ ! -f "$SESSION" ]]; then
  echo "未找到会话文件。诊断信息：" >&2
  echo "  ~/.codex/sessions 是否存在: $([[ -d "$HOME/.codex/sessions" ]] && echo yes || echo no)" >&2
  echo "  目录下 jsonl 数量: $(find "$HOME/.codex/sessions" -name '*.jsonl' 2>/dev/null | wc -l)" >&2
  echo "  可手动指定: ./export-session.sh /path/to/rollout.jsonl" >&2
  exit 1
fi
echo "使用会话: $SESSION" >&2

OUT="HANDOFF_$(date +%Y%m%d_%H%M%S).md"

# 提取文本：兼容 Claude(.message.content) 与 Codex(.payload/.content/任意 .text)
TEXT=$(jq -r '
  ( .message.content? // .payload.content? // .content? // empty ) as $c
  | if   ($c|type)=="array"  then ($c[] | (.text? // .content? // empty))
    elif ($c|type)=="string" then $c
    else (.. | .text? // empty) end
' "$SESSION" 2>/dev/null | sed '/^$/d')

# 关键命令
CMDS=$(jq -r '.. | (.command? // empty) | if type=="array" then join(" ") else . end' "$SESSION" 2>/dev/null \
       | sed '/^$/d' | sort -u | head -40)

# 关键文件
FILES=$( { jq -r '.. | (.file_path? // .path? // empty)' "$SESSION" 2>/dev/null;
           echo "$TEXT" | grep -oE '(/|\./|~/|[A-Za-z]:\\)[^ "'\''`]+\.[A-Za-z0-9]+'; } \
         | sed '/^$/d' | sort -u | head -40)

{
echo "# 会话交接摘要"
echo
echo "> 来源: \`$SESSION\`  导出时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo
echo "## 目标"
echo "- [ ] 待补：本次会话要达成什么 / 成功标准"
echo
echo "## 已完成"
echo "- [ ] 待补（参考下方对话原文与已改文件）"
echo
echo "## 未完成"
echo "- [ ] 待补"
echo
echo "## 关键文件"
[[ -n "$FILES" ]] && echo "$FILES" | sed 's/^/- `/; s/$/`/' || echo "- （未自动识别到）"
echo
echo "## 关键命令"
echo '```bash'
[[ -n "$CMDS" ]] && echo "$CMDS" || echo "# （未自动识别到）"
echo '```'
echo
echo "## 风险点"
echo "- [ ] 待补：未验证项 / 已知报错 / 不确定的决策"
echo
echo "## 下一步"
echo "1. 待补"
echo "2. 待补"
echo
echo "---"
echo "## 对话原文（供续接，按需删减）"
echo '```text'
echo "$TEXT" | tail -200
echo '```'
} > "$OUT"

echo "已生成: $OUT" >&2