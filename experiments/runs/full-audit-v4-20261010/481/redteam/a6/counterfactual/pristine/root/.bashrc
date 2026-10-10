# .bashrc - User shell customizations

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
