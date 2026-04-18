#!/bin/bash
# Run database migrations for supervisor-api

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load virtual environment if exists
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d "../supervisor-venv" ]; then
    source ../supervisor-venv/bin/activate
fi

# Check if Python is available
if ! command -v python &> /dev/null; then
    echo "Python not found. Please activate virtual environment first."
    exit 1
fi

# Parse arguments
COMMAND=${1:-run}
shift || true

case "$COMMAND" in
    check)
        echo "Checking migration status..."
        python run_migration.py --check
        ;;
    run)
        echo "Running migrations..."
        python run_migration.py "$@"
        ;;
    dry-run)
        echo "Dry run mode..."
        python run_migration.py --dry-run
        ;;
    --file)
        echo "Running specific migration: $1"
        python run_migration.py --file "$1"
        ;;
    *)
        echo "Usage: $0 [check|run|dry-run|--file <migration.sql>]"
        echo ""
        echo "Commands:"
        echo "  check         Check migration status"
        echo "  run           Run all pending migrations"
        echo "  dry-run       Preview migrations without applying"
        echo "  --file <file> Run specific migration file"
        exit 1
        ;;
esac