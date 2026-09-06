# vhs tape for docs/demo.gif — run with `vhs docs/demo.tape` from the repo root.
# The replay is only typed as a comment on purpose: a real replay spends tokens.

Output docs/demo.gif

Set Shell "zsh"
Set FontSize 14
Set Width 1000
Set Height 720
Set Theme "Catppuccin Mocha"
Set TypingSpeed 30ms

Hide
Type "setopt interactive_comments; PS1='%F{cyan}claude-drift%f $ '; clear"
Enter
Sleep 1s
Show

Type "uv run drift scan"
Enter
Sleep 3s

Type "clear"
Enter
Sleep 500ms

Type "# drift replay --from opus-5 --to sonnet --turns 20 --self-replays 5  (earlier: 20 min)"
Enter
Sleep 1s

Type "uv run drift report --run 20260906-095910"
Enter
Sleep 7s
