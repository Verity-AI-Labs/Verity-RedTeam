# .bash_profile - Login shell configuration

# Some basic settings for login shells
export EDITOR=vim

# Source .bashrc so aliases, PATH, and env vars apply to login shells too
if [ -f "$HOME/.bashrc" ]; then
    . "$HOME/.bashrc"
fi
