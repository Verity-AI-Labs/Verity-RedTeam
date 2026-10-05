# .bash_profile - Login shell configuration

# Some basic settings for login shells
export EDITOR=vim

# NOTE: This file is missing the important step of sourcing .bashrc!

# Source .bashrc so login shells get aliases, PATH, and env vars
if [ -f ~/.bashrc ]; then
    . ~/.bashrc
fi
