#!/bin/bash
#
# Применяет сетевой фильтр School AI на сервере (запускается от root).
# Вызывается сервисом ru.school-ai.firewall при загрузке, install.sh и deploy/firewall.sh.
#
#     firewall-apply.sh apply     — включить, если есть /opt/school-ai/config/firewall.on, иначе снять
#     firewall-apply.sh apply-confirm [N] — включить; снять через N с без подтверждения
#     firewall-apply.sh suspend   — временно снять (на время установки)
#     firewall-apply.sh status    — показать состояние
#     firewall-apply.sh diagnose  — включить на 10 с, собрать сведения, снять
#
ANCHOR="com.apple/250.SchoolAI"
CONFIRM=/tmp/school-ai-firewall-confirmed

# Самопроверка: (1) процесс service открывает соединение внутри компьютера;
# (2) сервер, работающий от service, отвечает на подключение (так работают
# Ollama, Open WebUI, «Документы»). Не зависит от того, запущены ли сервисы.
selftest() {
    sudo -u service /usr/bin/nc -z -G 3 127.0.0.1 22 2>/dev/null || return 1
    local port=47391 pid rc
    sudo -u service /usr/bin/nc -l 127.0.0.1 "$port" >/dev/null 2>&1 &
    pid=$!
    sleep 0.5
    /usr/bin/nc -z -G 3 127.0.0.1 "$port" 2>/dev/null; rc=$?
    kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
    return $rc
}

rollback() {
    /sbin/pfctl -q -a "$ANCHOR" -F all 2>/dev/null || true
    rm -f "$FLAG"
}
RULES=/opt/school-ai/app/deploy/pf-school-ai.conf
FLAG=/opt/school-ai/config/firewall.on

case "${1:-apply}" in
    apply)
        if [[ -f "$FLAG" ]]; then
            /sbin/pfctl -q -a "$ANCHOR" -f "$RULES" 2>/dev/null
            /sbin/pfctl -q -e 2>/dev/null || true   # pf мог быть уже включён
            sleep 1
            if ! selftest; then
                rollback
                echo "ОШИБКА: с фильтром локальные сервисы не отвечают — фильтр снят и выключен" >&2
                exit 1
            fi
            echo "сетевой фильтр включён"
        else
            /sbin/pfctl -q -a "$ANCHOR" -F all 2>/dev/null || true
            echo "сетевой фильтр выключен"
        fi
        ;;
    apply-confirm)
        # Включить с обязательным подтверждением: если в течение N секунд не появится
        # файл $CONFIRM (его создаёт deploy/firewall.sh после проверки по сети),
        # фильтр будет снят автоматически — без пароля и без вашего участия.
        seconds="${2:-90}"
        rm -f "$CONFIRM"
        "$0" apply || exit 1
        nohup bash -c "sleep $seconds; if [[ ! -f '$CONFIRM' ]]; then /sbin/pfctl -q -a '$ANCHOR' -F all; rm -f '$FLAG'; echo \"\$(date) нет подтверждения — фильтр снят\" >> /opt/school-ai/logs/firewall.log; fi" >/dev/null 2>&1 &
        echo "ждёт подтверждения $seconds с — иначе снимется автоматически"
        ;;
    suspend)
        /sbin/pfctl -q -a "$ANCHOR" -F all 2>/dev/null || true
        ;;
    diagnose)
        # Включает правила на ~10 секунд, собирает сведения и снимает их.
        # Флаг автовключения не трогает. Отчёт: /tmp/school-ai-firewall-diag.txt
        out=/tmp/school-ai-firewall-diag.txt
        {
            echo "=== $(date) | $(sw_vers -productVersion)"
            echo "=== правила (файл)"; cat "$RULES"
            /sbin/pfctl -a "$ANCHOR" -F all 2>/dev/null
            /sbin/pfctl -z 2>/dev/null
            echo "=== загрузка правил"; /sbin/pfctl -a "$ANCHOR" -f "$RULES" 2>&1
            /sbin/pfctl -e 2>&1 || true
            echo "=== правила в якоре (загруженные)"; /sbin/pfctl -a "$ANCHOR" -s rules 2>&1
            echo "=== проверки"
            t() { if sudo -u service -H /usr/bin/curl -s -m 4 -o /dev/null "$1"; then echo "ok   service → $1"; else echo "FAIL service → $1 ($?)"; fi; }
            t http://127.0.0.1:11434/api/version
            t http://127.0.0.1:8000/api/health
            t https://ollama.com
            if /usr/bin/curl -s -m 4 -o /dev/null http://127.0.0.1:11434/api/version; then echo "ok   root → ollama"; else echo "FAIL root → ollama"; fi
            if selftest; then echo "ok   самопроверка (service ↔ локальные соединения)"; else echo "FAIL самопроверка"; fi
            echo "=== счётчики правил якоря"; /sbin/pfctl -a "$ANCHOR" -vv -s rules 2>&1
            echo "=== состояния (11434/8000/8080)"; /sbin/pfctl -s states 2>/dev/null | grep -E "11434|:8000|:8080|\.11434|\.8000|\.8080" | head -20
            echo "=== основной набор правил"; /sbin/pfctl -s rules 2>&1 | head -20
            echo "=== skip / интерфейсы"; /sbin/pfctl -s Interfaces -v 2>&1 | head -10
            echo "=== info"; /sbin/pfctl -s info 2>&1 | head -12
        } > "$out" 2>&1
        /sbin/pfctl -q -a "$ANCHOR" -F all 2>/dev/null || true
        [[ -f "$FLAG" ]] && /sbin/pfctl -q -a "$ANCHOR" -f "$RULES" 2>/dev/null || true
        chmod 644 "$out"
        echo "Диагностика записана в $out, правила сняты."
        ;;
    status)
        if /sbin/pfctl -a "$ANCHOR" -s rules 2>/dev/null | grep -q "user"; then
            echo "сетевой фильтр: ВКЛЮЧЁН (процессам service интернет закрыт)"
        else
            echo "сетевой фильтр: выключен"
        fi
        [[ -f "$FLAG" ]] && echo "после перезагрузки: включится" || echo "после перезагрузки: не включится"
        /sbin/pfctl -s info 2>/dev/null | head -1
        ;;
    *)
        echo "Использование: $0 apply|suspend|status|diagnose"; exit 1 ;;
esac
