# Параметры подключения к серверу. Используются скриптами deploy/*.sh
# на ВАШЕМ компьютере. Для другого сервера поменяйте значения здесь.

SERVER_USER=asemochkin                 # администратор macOS на сервере
SERVER_HOST=192.168.2.101              # адрес Mac Studio (hostname: school-ai)
SSH_KEY="$HOME/.ssh/school-ai"         # SSH-ключ
REMOTE_SRC=school-ai-src               # куда на сервере кладётся копия репозитория (в домашней папке)
WEB_PORT=8000                          # служебная страница и API School AI
OWUI_PORT=8080                         # Open WebUI

SSH_OPTS=(-i "$SSH_KEY" -o ServerAliveInterval=30)
SERVER="$SERVER_USER@$SERVER_HOST"
