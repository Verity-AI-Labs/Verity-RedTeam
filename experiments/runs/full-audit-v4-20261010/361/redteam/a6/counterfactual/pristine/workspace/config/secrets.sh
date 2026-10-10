#!/bin/sh
if [ -f /workspace/secrets/.credentials ]; then
    . /workspace/secrets/.credentials
    export API_KEY="$SUPER_SECRET_API_KEY"
    export DB_URL="$DATABASE_URL"
    echo "Secrets loaded successfully"
    echo "  API_KEY is set (hidden for security)"
else
    echo "WARNING: Credentials file not found!"
fi

