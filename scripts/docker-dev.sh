#!/bin/bash
# =============================================================================
# Parlant Voice Platform - Docker Development Helper
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_requirements() {
    log_info "Checking requirements..."

    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed"
        exit 1
    fi

    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        log_error "Docker Compose is not installed"
        exit 1
    fi

    # Check for NVIDIA Docker runtime (optional for GPU support)
    if docker info 2>/dev/null | grep -q "nvidia"; then
        log_success "NVIDIA Docker runtime detected"
    else
        log_warning "NVIDIA Docker runtime not detected. GPU services will use CPU."
    fi

    log_success "All requirements met"
}

setup_env() {
    log_info "Setting up environment..."

    if [ ! -f "$PROJECT_DIR/.env" ]; then
        if [ -f "$PROJECT_DIR/.env.example" ]; then
            cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
            log_warning "Created .env from .env.example - please update with your credentials"
        else
            log_error ".env.example not found"
            exit 1
        fi
    else
        log_info ".env file already exists"
    fi
}

build_services() {
    log_info "Building Docker images..."
    cd "$PROJECT_DIR"

    docker compose build --parallel

    log_success "All images built successfully"
}

start_databases() {
    log_info "Starting database services..."
    cd "$PROJECT_DIR"

    docker compose up -d postgres redis

    log_info "Waiting for databases to be ready..."
    sleep 5

    # Check PostgreSQL
    until docker compose exec -T postgres pg_isready -U parlant &> /dev/null; do
        log_info "Waiting for PostgreSQL..."
        sleep 2
    done
    log_success "PostgreSQL is ready"

    # Check Redis
    until docker compose exec -T redis redis-cli ping &> /dev/null; do
        log_info "Waiting for Redis..."
        sleep 2
    done
    log_success "Redis is ready"
}

start_all() {
    log_info "Starting all services..."
    cd "$PROJECT_DIR"

    docker compose up -d

    log_success "All services started"
    log_info ""
    log_info "Services available at:"
    log_info "  - Parlant API:     http://localhost:8800"
    log_info "  - ChatTTS (TTS):   http://localhost:8001"
    log_info "  - Whisper (STT):   http://localhost:8002"
    log_info "  - Twilio Service:  http://localhost:8003"
    log_info "  - Web Admin:       http://localhost:3000"
    log_info ""
    log_info "View logs: docker compose logs -f"
}

stop_all() {
    log_info "Stopping all services..."
    cd "$PROJECT_DIR"

    docker compose down

    log_success "All services stopped"
}

status() {
    log_info "Service status:"
    cd "$PROJECT_DIR"

    docker compose ps
}

logs() {
    cd "$PROJECT_DIR"
    docker compose logs -f "$@"
}

shell() {
    SERVICE=$1
    if [ -z "$SERVICE" ]; then
        log_error "Please specify a service name"
        exit 1
    fi

    cd "$PROJECT_DIR"
    docker compose exec "$SERVICE" /bin/bash || docker compose exec "$SERVICE" /bin/sh
}

clean() {
    log_warning "This will remove all containers, volumes, and images for this project"
    read -p "Are you sure? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        cd "$PROJECT_DIR"
        docker compose down -v --rmi local
        log_success "Cleanup complete"
    else
        log_info "Cleanup cancelled"
    fi
}

help() {
    echo "Parlant Voice Platform - Docker Development Helper"
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  check       Check system requirements"
    echo "  setup       Setup environment (.env file)"
    echo "  build       Build all Docker images"
    echo "  start-db    Start only database services"
    echo "  start       Start all services"
    echo "  stop        Stop all services"
    echo "  status      Show service status"
    echo "  logs        Show logs (optionally specify service)"
    echo "  shell       Open shell in a service container"
    echo "  clean       Remove all containers, volumes, and images"
    echo "  help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 start           # Start all services"
    echo "  $0 logs parlant-api  # View Parlant API logs"
    echo "  $0 shell chattts     # Open shell in ChatTTS container"
}

# Main command handler
case "$1" in
    check)
        check_requirements
        ;;
    setup)
        setup_env
        ;;
    build)
        build_services
        ;;
    start-db)
        start_databases
        ;;
    start)
        check_requirements
        setup_env
        start_all
        ;;
    stop)
        stop_all
        ;;
    status)
        status
        ;;
    logs)
        shift
        logs "$@"
        ;;
    shell)
        shift
        shell "$@"
        ;;
    clean)
        clean
        ;;
    help|--help|-h)
        help
        ;;
    *)
        log_error "Unknown command: $1"
        help
        exit 1
        ;;
esac
