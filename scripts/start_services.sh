#!/usr/bin/env bash
# Bring the scheduler and the bot up on boot.
#
# Without this the machine only runs while somebody remembers to start it by
# hand, and a rebuilt VM comes back silent: no coupon is pushed, no result is
# polled, and nothing says so.
#
# Idempotent on purpose. Being asked twice must not leave two schedulers
# racing to push the same coupon.

set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${HOME}/.otomasyon/logs"
mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_DIR/start.log"; }

# Name every missing variable at once. Reporting them one boot at a time
# would send the reader round the loop again for the second one.
missing=()
for var in TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_USERNAME; do
    [ -z "${!var:-}" ] && missing+=("$var")
done
if [ ${#missing[@]} -gt 0 ]; then
    log "Eksik ortam degiskeni: ${missing[*]}"
    log "Zamanlayici ve bot baslatilmadi. Bu degerleri sohbetin yanindaki"
    log "Secrets bolumune ekleyin; makine bir sonraki acilista kendi kalkar."
    exit 0
fi

start_one() {
    local name="$1"
    shift
    if pgrep -f "otomasyon.cli $name" >/dev/null 2>&1; then
        log "$name zaten calisiyor, yeniden baslatilmadi."
        return
    fi
    cd "$REPO" || exit 1
    nohup python3 -u -m otomasyon.cli "$@" >>"$LOG_DIR/$name.log" 2>&1 &
    log "$name baslatildi (pid $!)"
}

start_one scheduler scheduler --interval 25
start_one bot bot
