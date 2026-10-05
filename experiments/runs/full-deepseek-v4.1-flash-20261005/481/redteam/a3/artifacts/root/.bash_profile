# .bash_profile - Login shell configuration

# Some basic settings for login shells
export EDITOR=vim

# NOTE: This file is missing the important step of sourcing .bashrc!

# Source .bashrc for login shells
if [ -f ~/.bashrc ]; then
    . ~/.bashrc
fi
