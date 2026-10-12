# .bash_profile - Login shell configuration

# Some basic settings for login shells
export EDITOR=vim

# NOTE: .bashrc is pulled in below (equivalent to: source ~/.bashrc)
# Inlined ~/.bashrc customizations so login shells inherit them.

# Environment variables
export DEV_MODE=development

# PATH modifications
export PATH="/opt/custom-tools/bin:$PATH"

# Custom prompt
export PS1="[\u@\h \W]\$ "

# Source bash_aliases if it exists
if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi
