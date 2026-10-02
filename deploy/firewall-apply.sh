#!/bin/bash
#
# Применяет сетевой фильтр School AI на сервере (запускается от root).
# Вызывается сервисом ru.school-ai.firewall при загрузке, install.sh и deploy/firewall.sh.
#
#     firewall-apply.sh apply     — включить, если есть /opt/school-ai/config/firewall.on, иначе снять
#     firewall-apply.sh suspend   — временно снять (на время установки)
#     firewall-apply.sh status    — показать состояние
#
ANCHOR="com.apple/250.SchoolAI"
RULES=/opt/school-ai/app/deploy/pf-school-ai.conf
FLAG=/opt/school-ai/config/firewall.on

case "${1:-apply}" in
    apply)
        if [[ -f "$FLAG" ]]; then
            /sbin/pfctl -q -a "$ANCHOR" -f "$RULES"
            /sbin/pfctl -q -e 2>/dev/null || true   # pf мог быть уже включён
            echo "сетевой фильтр включён"
        else
            /sbin/pfctl -q -a "$ANCHOR" -F all 2>/dev/null || true
            echo "сетевой фильтр выключен"
        fi
        ;;
    suspend)
        /sbin/pfctl -q -a "$ANCHOR" -F all 2>/dev/null || true
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
        echo "Использование: $0 apply|suspend|status"; exit 1 ;;
esac
