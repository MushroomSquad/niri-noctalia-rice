# Local additions; Garuda provides the shared configuration, ~/.local/bin and depot_tools.
# Do not use bash-style `export PATH=$PATH:...` in fish — it corrupts PATH.

type -q direnv; and direnv hook fish | source

abbr cat 'bat --style header,snip,changes'

# Use the rice-selected fastfetch preset for interactive sessions.
if status --is-interactive && type -q fastfetch
   fastfetch
end

fish_add_path "$HOME/.local/bin"
