# niri-noctalia-rice

Моё оформление CachyOS: **niri + Noctalia**, **wezterm**, **fish + starship**, экран входа **greetd/ReGreet**, обои и компоненты **Garuda Mokka**. Цветами управляет Noctalia; Mokka добавляет темы, шрифты и настройки приложений.

Перед установкой обнови систему: `sudo pacman -Syu`. Сам установщик ставит пакеты через `pacman -S --needed --noconfirm`, без обновления системы. Нужен обычный пользователь с sudo, Arch/CachyOS; для GUI — Python, PyGObject, GTK4 и libadwaita.

```bash
git clone <repo> && cd niri-noctalia-rice
./install.sh
# Или выбрать компоненты в окне:
./install.sh --gui
```

Необязательные части спрашиваются интерактивно; без TTY или с `--yes` берутся значения по умолчанию. Пакеты, greeter, обои, Handy, Neovim, агенты и Mokka включены, FireDragon и тема подсветки fish выключены.

- `--no-pkgs`, `--no-greeter`, `--no-handy`, `--no-wallpapers` — пропустить соответствующие шаги. `--no-pkgs` также передаёт rice `--no-packages`.
- `--no-mokka` — пропустить финальное применение компонентов. `--gui` — открыть выбор компонентов вместо автоматического `apply --defaults`; выбор пакетов и компонентов делается в самом GUI.
- `--no-nvim` / `--no-agents` передаются в rice как `--without nvim,agents` (или один выбранный компонент). `--nvim` / `--agents` явно включают их через `--with`. Neovim с AstroNvim и агенты теперь ставятся рецептами, а не копируются установщиком отдельно. Плагины AstroNvim подтянутся при первом запуске; агенты GSD требуют отдельной установки `~/.codex/get-shit-done`.
- `--firedragon` — добавить через `--with firedragon` браузер из chaotic-aur/AUR (paru/yay); профиль не переносится. Эти флаги компонентов действуют при автоматическом apply; при `--no-mokka` компоненты не ставятся.
- `--fish-theme` — сохранить Catppuccin Mocha для подсветки fish. Конфиг fish, prompt Starship и Fastfetch теперь управляются рецептами rice.
- `--only pkgs,greeter,handy` — выполнить только перечисленные шаги (можно один или несколько). Без вопросов, копирования дерева конфигов, gsettings и любых вызовов rice. Этот режим также используют рецепты приложений, поэтому рекурсии нет.
- `RICE_NO_RUN=1 ./install.sh ...` — передать финальному `rice apply` флаг `--no-run`: пропустить команды рецептов после записи файлов. Это не отключает обычные действия установщика, установку пакетов или сборку тем. Для `--gui` этот режим не применяется: у GUI нет такого CLI-флага.

Handy скачивается с GitHub (последняя версия или `HANDY_VERSION=0.8.3`), ставится в `~/.local/opt/handy`, симлинк — `~/.local/bin/handy`. Настройки PTT Ctrl+Space и `handy-paste` через wl-copy+wtype кладутся только при первом запуске установщика без существующих настроек Handy; строка автозапуска не дублируется. Модели выбираются в окне Handy, в репозитории их нет.

Перезаписанные установщиком файлы сохраняются как `*.bak-<время>`. После первого входа в niri выполни `noctalia msg templates-apply`. В меню приложений появится **niri-noctalia-rice**: ярлык запускает GUI из каталога репозитория, поэтому оставь checkout на месте.

Для операций Noctalia нужна запущенная Noctalia (Noctalia must be running). При первой установке они остаются в состоянии `run_pending`, пока внутри сессии не выполнится `python -m rice apply --defaults` из каталога репозитория или **Apply** в GUI.

## Компоненты Mokka

| Компоненты | Что меняют |
|---|---|
| `palette` | палитра Mokka и актуальные шаблоны Noctalia |
| `qt`, `kde-apps` | Kvantum Mokka, qt6ct и оформление KDE-приложений |
| `gtk` | Noctalia/adw-gtk3 по умолчанию или Colloid Catppuccin |
| `fonts`, `cursor`, `niri-look` | Inter + JetBrainsMono Nerd, курсор Mocha Mauve, радиус окон 16 |
| `fish`, `starship` | Garuda fish; Starship Mokka (по умолчанию), Garuda или живые цвета Noctalia |
| `fastfetch`, `wallpapers` | Fastfetch Mokka или пресет репозитория; обои Mokka или коллекция Garuda |
| `konsole-kate`, `yakuake`, `bat`, `micro` | темы приложений; настройки применяются с учётом наличия приложения |
| `nvim`, `agents` | AstroNvim и агенты Claude Code / Codex |
| `firedragon` | необязательный браузер, по умолчанию выключен |
| `greeter`, `handy`, `base-packages` | вызов `install.sh --only ...` в терминале; только по явному выбору |

`apply --defaults` применяет уже включённые компоненты и компоненты по умолчанию, ещё не имеющие записи в состоянии. Выключенные пользователем остаются выключенными. Шаги `greeter`, `handy`, `base-packages` исключены из этого набора: включай их явно или через GUI. Их выключение сохраняет текущие настройки, уже установленные приложения остаются.

## Управление

Команды запускаются из каталога репозитория. `apply` сам скачивает недостающие закреплённые исходники; после загрузки их можно использовать офлайн. Перед обновлением исходников показывается diff, обновления проверяются по запросу, таймера нет.

```bash
python -m rice --help
python -m rice gui
python -m rice status
python -m rice status --remote
python -m rice apply --defaults --dry-run
python -m rice apply --defaults --yes
python -m rice apply --defaults --without nvim,agents --with firedragon --yes
python -m rice apply gtk --variant gtk=colloid --yes
python -m rice apply starship --variant starship=noctalia --yes
python -m rice disable gtk
python -m rice fetch --defaults
python -m rice update
# Перезакрепить исходники отдельно:
python -m rice lock --latest
# Или: python -m rice lock --pkgbuilds <commit>
```

Отдельной команды build или diff нет: просмотр изменений — `apply --dry-run` (без сети и сборки). Colloid собирается запуском скачанного upstream `install.sh` только при явном выборе этого варианта; приложения libadwaita эту тему игнорируют.

Noctalia управляет цветами GTK и Qt. Kvantum остаётся Mokka при смене палитры Noctalia. Для логотипа Fastfetch используется kitty graphics, поддержка включена в wezterm.

## Кто владеет файлами

```bash
python -m rice owned
python -m rice refresh .config/niri/cfg/misc.kdl
```

`rice owned` выводит пути относительно домашнего каталога: целые файлы, начальные копии и цели точечных правок. До первого применения список пуст. При повторном запуске `install_tree` сохраняет эти файлы и пишет `kept (owned by rice): <path>`. После выключения компонента пути остаются в списке, чтобы установщик не сбрасывал их.

Rice проверяет хеши целых файлов и сохраняет ручные правки без `--force`; общие конфиги правит точечно, не перезаписывая цвета Noctalia. Начальные копии (например, `lazy-lock.json` Neovim и Starship в варианте Noctalia) не отслеживаются по хешу. `python -m rice refresh <relpath>` возвращает версию из `home/<relpath>` с резервной копией и повторно применяет включённые точечные правки. Команда подходит для файлов, у которых есть копия в `home/`.

## Свои настройки

Храни свои дополнения в `~/.config/rice-custom/`: rice и полный `install.sh` создают только отсутствующие примеры из `mokka/custom-skel/`. Существующие файлы не перезаписываются и не копируются в резервные копии; каталог не входит в `rice owned` и не проходит через `install_tree`. `--dry-run` его не создаёт. Кнопка **Open custom folder** в GUI создаёт и открывает этот каталог.

- `fish/*.fish` загружаются по имени в сортированном порядке последними, поэтому могут переопределять alias из Garuda. Например: `alias ls 'echo custom'`.
- `niri.kdl` подключается после `noctalia.kdl`; здесь можно переопределять привязки клавиш. Опечатка в `rice-custom/niri.kdl` ломает весь конфиг niri: проверяй изменения командой `niri validate`. Если `~/.config/niri` — симлинк, относительный include может молча пропустить этот файл.
- `wezterm.lua` начинается с `local config = ...`; далее можно менять поля, например `config.font_size = 14`. Файл загружается перед `return config` и добавляется в список наблюдения WezTerm.
- `gtk3.css` и `gtk4.css` содержат дополнения GTK 3 и GTK 4 после основных стилей. В варианте Colloid подключаются оба файла; держи в них совместимые с нужной версией правила.
- Необязательный `starship.toml` **заменяет prompt целиком** через `STARSHIP_CONFIG`. По умолчанию есть только незагружаемый `starship.toml.example`; для своей темы создай полный `starship.toml` и открой новый fish.

В `kdeglobals`, `dolphinrc`, `qt6ct.conf`, `kvantum.kvconfig`, `micro/settings.json` и `noctalia/settings.toml` rice меняет только нужные ключи, сохраняя остальные пользовательские настройки.

## Что внутри

| Путь | Назначение |
|---|---|
| `home/.config/niri` | конфиг niri; `noctalia.kdl` генерирует Noctalia, `cfg/rice.kdl` — rice |
| `home/.config/noctalia` | базовый конфиг, палитра Catppuccin-Lavender, `plugins.json` |
| `home/.config/wezterm`, `home/.config/gtk-{3,4}.0` | базовые настройки терминала и GTK |
| `mokka/custom-skel/` | примеры для личного каталога `~/.config/rice-custom/` |
| `rice/`, `mokka/components/` | общий движок CLI/GUI и декларативные рецепты |
| `mokka/files/`, `mokka/upstream.lock.toml` | файлы вариантов и закреплённые upstream-источники |
| `nvim/`, `agents/` | исходники для рецептов приложений |
| `wallpapers/`, `handy/`, `etc/greetd/` | обои репозитория, шаблон Handy, greetd/ReGreet |

Мониторы определяются через `~/.local/bin/niri-autodisplay` при старте niri: родное разрешение, максимальная частота, VRR, масштаб по DPI, слева направо. Убери строку AUTOGEN в `display.kdl`, чтобы зафиксировать вручную; поворот не определяется, пример — `display.kdl.example`. Greeter генерирует outputs по подключённым портам.

Плагины Noctalia ставятся отдельно; `fish_variables`, старый `settings.json` v4, кэш и состояние не переносятся. Fingerprint в lockscreen включён, но нужен `fprintd` + `fprintd-enroll`.
