# SPEC: перенос Garuda Mokka в niri-noctalia-rice

Статус: черновик v1 (2026-11-16). Вход для плана → консенсус critic/architect → verifier → реализация.

## 1. Цель

Взять максимум узнаваемого оформления Garuda Mokka (Catppuccin Mocha **Mauve**) поверх CachyOS + niri + Noctalia,
**не вендоря** файлы Garuda в этот репозиторий: при установке исходники скачиваются из апстрима на закреплённой
версии, к ним применяются наши правила адаптации, результат ставится в `$HOME` (и только по явному выбору в систему).
При обновлении апстрима показываем разницу и пересобираем только изменившееся. Выбор и превью — в нативном GTK-окне.

## 2. Не-цели

- Plasma/KWin-специфика: layout-templates, Panel Colorizer, Aurorae, kwinrc, kscreenlockerrc, look-and-feel —
  не ставим (превью Panel Colorizer можно показывать как референс).
- GRUB, цвета TTY (`vt.default_*`), Plasma Login Manager/SDDM, `garuda-common-settings`, `garuda-update`,
  `initial-setup.*` — не трогаем. GRUB — возможный отдельный компонент позже, по умолчанию выключен и вне v1.
- Пакет `garuda-mokka` как зависимость — не ставим.
- Пиксельное совпадение с Garuda не требуется.

## 3. Апстрим и правило закрепления версий

Правило (решение пользователя): **если у компонента есть релиз/тег или PKGBUILD Garuda фиксирует версию/коммит —
закрепляемся ровно на нём; если нет — берём ветку, из которой это берёт сама Garuda.**

Корень доверия — репозиторий `gitlab.com/garuda-linux/pkgbuilds`, закреплённый на коммите. Из его PKGBUILD-ов
автоматически выводятся остальные пины:

| Источник | Откуда пин | Текущее значение (на pkgbuilds `3d68043`) |
|---|---|---|
| `garuda-linux/pkgbuilds` (сами файлы `garuda-fish-config/*`, `garuda-starship-prompt/starship.toml`) | коммит репо | `3d68043b…` |
| `garuda-mokka` | `_commit` в `garuda-mokka/PKGBUILD` (pkgver 2.0.9, тег `2.0.9`) | `7e0a1c75…` |
| `catppuccin/kde`, `micro`, `conky`, `konsole`, `bat`, `grub` | `#commit=` в `source=` того же PKGBUILD | da69a64, 015a2bb, eff06f7, 3b64040, 6810349, 0a37ab1 |
| `vinceliuice/Colloid-gtk-theme` | `#commit=` там же | `fd805dbe…` |
| `garuda-wallpapers` | `_commit` в его PKGBUILD | `a0f80847…` |
| `garuda-icons` (лого fastfetch `mokka-fastfetch.png`) | `_commit` в его PKGBUILD | `17e1dfa0…` |
| `franciscolourenco/done` | тег в URL `source=` garuda-fish-config | `1.21.1` |

`sha256sums` из PKGBUILD используются для проверки tar-архивов, где они не `SKIP`.

### 3.1 Lock-файл

`mokka/upstream.lock.toml` в репозитории — единственное место с пинами. Генерируется командой `lock` из PKGBUILD-ов
на заданном коммите pkgbuilds; руками не правится. На каждый источник: `url`, `kind` (gitlab-archive | github-archive |
pkgbuilds-path | raw-file), `ref` (коммит/тег), `sha256` (если известен), `derived_from` (какой PKGBUILD и строка).

## 4. Архитектура

```
mokka/
  upstream.lock.toml      # пины (генерируется)
  components/<id>.toml    # декларативный рецепт компонента
  previews/<id>/*.png     # наши скриншоты превью
rice/                     # python-пакет (stdlib + PyGObject), без новых pip-зависимостей
  lock.py   fetch.py   recipe.py   state.py   cli.py   gui.py
install.sh                # остаётся bootstrap'ом; новый шаг вызывает `python -m rice apply ...`
```

- **Кэш** скачанного: `~/.cache/niri-noctalia-rice/upstream/<source>/<ref>/` (распакованное дерево). Повторная
  установка той же версии — без сети.
- **Сборка**: рецепт применяется к кэшу → staging `~/.cache/niri-noctalia-rice/build/<component>/` (зеркало целевых
  путей). Установка из staging — та же семантика, что `install_tree`: существующий отличающийся файл → `*.bak-<ts>`.
- **Состояние**: `~/.local/state/niri-noctalia-rice/state.json`: для каждого установленного компонента — refs
  источников, хэш рецепта, sha256 каждого установленного файла. Нужен для «что изменилось» и для обнаружения
  ручных правок пользователя (такие файлы не перезаписываются молча — предупреждение + diff).

### 4.1 Рецепт компонента (TOML)

Поля: `id`, `title`, `description`, `group` (look | qt | gtk | shell | terminal | apps | wallpapers),
`default` (bool), `conflicts` ([id]), `requires` ([id]), `packages.repo` / `packages.aur` ([pkg]),
`only_if_installed` (pkg — например `yakuake`), `previews` ([path]), `sources` ([lock-id]), `steps` ([op]).

Набор операций (минимальный, расширяется только по нужде):

| op | Смысл |
|---|---|
| `copy` | файл/каталог из источника → цель в staging |
| `replace` | regex-замена в файле (пути `/usr/share/...` → `$HOME/...`, опции) |
| `drop_lines` | удалить строки по regex (опасные алиасы) |
| `ini_set` | точечно выставить ключи в INI/KConfig (kdeglobals, dolphinrc, katerc, qt6ct.conf) — **никогда** не заменять такие файлы целиком |
| `toml_set` / `json_set` | точечно в config.toml Noctalia / settings.json micro |
| `kdl_block` | вставить/заменить именованный блок в niri-конфиге, помеченный маркерами `// rice:<id> begin/end` |
| `generate` | вызвать python-генератор (палитра Noctalia из `Mokka.colors` + терминальные цвета catppuccin) |
| `run` | пост-команда в `$HOME` (`bat cache --build`, `fc-cache`) — без sudo |

`$HOME` подставляется при сборке, не хардкодится.

### 4.2 Поток

1. `lock [--pkgbuilds <commit>|--latest]` — разобрать PKGBUILD-ы → записать lock (в репо, коммитится).
2. `fetch` — скачать недостающие источники выбранных компонентов в кэш, проверить sha256.
3. `build <ids>` — рецепты → staging.
4. `diff <ids>` — staging vs установленное (unified diff текстовых файлов, список бинарных).
5. `apply <ids>` — пакеты (pacman/AUR helper, `--needed`, **без `-Syu`**), установка файлов с бэкапами, `run`-шаги,
   запись state.
6. `status` — по каждому компоненту: установлен? lock-ref == state-ref? рецепт изменился? файлы правились руками?
   Есть ли в апстриме pkgbuilds новее коммит с другими пинами (сетевой check, опционально).
7. `update` = `lock --latest` → показать какие пины сдвинулись + diff → `build`/`apply` только затронутых.

CLI и GUI — тонкие слои над одними и теми же функциями.

## 5. Компоненты v1

Цвета ведёт Noctalia (вариант A): статические Mokka-файлы берутся там, где Noctalia-шаблона нет или он хуже.

| id | Что | Источник | Адаптация | default |
|---|---|---|---|---|
| `palette` | Noctalia-палитра `Mokka` (dark; light из Catppuccin Latte Mauve), `[theme] custom_palette="Mokka"`, включить шаблоны `kde`,`qt`,`gtk`,`niri` | `garuda-mokka:usr/share/color-schemes/Mokka.colors`, `catppuccin/konsole` mocha (терминал) | `generate` в формат `palettes/*.json` (как `Catppuccin-Lavender.json`: `dark`/`light`, `m*`-роли, `terminal`) | on |
| `fonts` | Inter для UI (Noctalia `font_family`, GTK, kdeglobals), JetBrains Mono Nerd — моно | пакеты `inter-font`, `ttf-jetbrains-mono-nerd` | `toml_set`/`ini_set`; Mokka `fontconfig/fonts.conf` **не** ставим | on |
| `cursor` | Catppuccin-Mocha-Mauve-Cursors | пакет `catppuccin-cursors-mocha` (chaotic-aur/AUR); `garuda-mokka:etc/skel/.icons/default/index.theme` | `kdl_block` `cursor {}` в niri, GTK settings.ini, gsettings | on |
| `qt` | Kvantum Mokka + qt6ct-kde, цвета Qt/Dolphin из Noctalia KColorScheme | `garuda-mokka:usr/share/Kvantum/{Mokka,CatppuccinMochaMauve}`, `etc/skel/.config/Kvantum/kvantum.kvconfig`; пакеты `kvantum`, `qt6ct-kde` | копировать в `~/.config/Kvantum`, `ini_set` qt6ct.conf, niri env `QT_QPA_PLATFORMTHEME qt6ct` (заменяет текущий `gtk3`) | on |
| `kde-apps` | `Mokka.colors` в `~/.local/share/color-schemes`; `kdeglobals` [Icons], шрифты; `dolphinrc` выбранные ключи (полный путь, Copy/Move меню, фильтр и т.п.) | `garuda-mokka:etc/skel/.config/{kdeglobals,dolphinrc}` | `ini_set` только перечисленных ключей; `dolphinui.rc` не ставим | on |
| `konsole-kate` | `Mokka.colorscheme`, `catppuccin-mocha.colorscheme` (Opacity 0.9 как в PKGBUILD), `Garuda.profile`, Kate `Mokka.theme` | garuda-mokka, catppuccin/konsole | `only_if_installed` konsole / kate | on |
| `yakuake` | скин mocha | garuda-mokka | `only_if_installed yakuake` | on |
| `fish` | конфиг Garuda | `pkgbuilds:garuda-fish-config/config.fish`, `done` 1.21.1 | `drop_lines`: `upd`, `fixpacman`, `rmpkg`, `mirror*`, `grubup`, `__garuda_fastfetch`-вызов; добавить наши `--icons --color=always`; локальные дополнения пользователя живут в `conf.d/rice-local.fish` и не перегенерируются | on |
| `starship` | выбор: `mokka` (`starship-mokka.toml`) / `garuda` (`garuda-starship-prompt`) / `noctalia` (текущий, палитра из шаблона) | garuda-mokka / pkgbuilds | взаимоисключающие варианты одного компонента (radio) | mokka |
| `fastfetch` | пресет `mokka.jsonc` + лого | garuda-mokka, `garuda-icons:mokka-fastfetch.png` | `replace` путь лого → `~/.local/share/icons/garuda/`; тип лого под wezterm (см. откр. вопрос 2) | on |
| `bat` | `Catppuccin Mocha.tmTheme` + `bat/config` | catppuccin/bat, garuda-mokka | `run: bat cache --build` | on |
| `micro` | `catppuccin-mocha.micro`, `colorscheme` в settings.json | catppuccin/micro | `json_set` | on |
| `gtk` | выбор: `noctalia` (шаблон gtk, текущий) / `colloid` (сборка Colloid `-n Catppuccin -t purple -c dark --tweaks catppuccin` в `~/.themes`, `sassc`) | Colloid | radio; при `colloid` выключить gtk-шаблон Noctalia | noctalia |
| `wallpapers` | Mokka (`City-Horizon`, `Mokka-tree`) + коллекция `garuda-wallpapers` | garuda-mokka, garuda-wallpapers | копировать в `~/Pictures/Wallpapers/garuda/`; опционально сделать Mokka-tree обоями по умолчанию | on (Mokka), off (коллекция) |
| `niri-look` | скругления окон 16 + clip, focus-ring из палитры (через niri-шаблон Noctalia, без хардкода цветов) | — (наш) | `kdl_block` в `rules.kdl`/`layout.kdl` | on |

Удаляются из текущего репо после миграции: опасные алиасы в `home/.config/fish/config.fish` (сам файл становится
генерируемым), `capitaine-cursors`, `Fira Sans` в gtk settings.ini.

## 6. GUI

- **Стек**: Python 3 + PyGObject + GTK4 + libadwaita (уже установлены). Без сборки, один процесс, запускается
  `python -m rice gui` и пунктом в `install.sh` (`--gui`) / `.desktop`-файлом.
- **Окно**: `Adw.NavigationSplitView`. Слева — группы компонентов (look, qt, gtk, shell, terminal, apps, wallpapers).
  Справа — карточки компонентов: заголовок, описание, переключатель (или radio для вариантов), бейджи
  «нужен пакет X (AUR)», «не установлено приложение — пропуск», «есть обновление», «файл изменён вручную».
- **Превью**: карусель картинок компонента (`Gtk.Picture`). Источники превью, по порядку:
  1) наши скриншоты в `mokka/previews/<id>/` (снимаются `niri msg action screenshot-window`/`grim` скриптом
  `rice/tools/capture_previews` после применения компонента);
  2) картинки из апстрима, где есть (скриншоты обоев Mokka, превью Panel Colorizer как референс);
  3) сгенерированный свотч палитры (для `palette`).
- **Действия**: «Проверить обновления» → список сдвинувшихся пинов; «Показать изменения» → diff-диалог (моно-шрифт);
  «Применить» → прогресс-лог (stdout шагов), sudo только для pacman через `pkexec`/терминал. Ошибка одного
  компонента не роняет остальные; итоговый отчёт.
- **Headless**: всё, что делает GUI, доступно в CLI; `install.sh` без TTY/с `--yes` работает как сейчас.

## 7. Совместимость с текущим install.sh

v1: `install.sh` остаётся как есть для базовых частей (пакеты niri/Noctalia, greeter, Handy, nvim, агенты) и в конце
вызывает `python -m rice apply --defaults` (или GUI с `--gui`). Перенос остальных шагов `install.sh` в компоненты —
v2, вне этой спеки (открытый вопрос 1).

## 8. Требования к качеству

- Идемпотентность: повторный `apply` без изменений апстрима/рецепта не меняет файлы и не плодит `.bak`.
- Никаких `sudo` кроме установки пакетов; никаких `pacman -Syu`, `-Rdd`, правки `/etc/default/grub`.
- Сеть только в `lock`/`fetch`/`update`; всё остальное работает офлайн из кэша.
- Скачанный код не исполняется, кроме Colloid `install.sh` в варианте `gtk=colloid` (явно отмечено в GUI).
- Тесты: `python -m unittest` на разбор PKGBUILD → lock, на каждую op рецепта, на идемпотентность apply
  во временном `$HOME`; фикстуры — урезанные реальные PKGBUILD-ы.

## 9. Открытые вопросы (для консенсуса)

1. Переносить ли в v1 существующие шаги `install.sh` (greeter, Handy, nvim, agents, firedragon) в компоненты, чтобы
   GUI выбирал «список устанавливаемых приложений» целиком? Пользователь упоминал «выбор списка устанавливаемых
   приложений» — склоняюсь к **да, как компоненты-обёртки над существующими функциями install.sh**.
2. Fastfetch-лого типа `kitty` в wezterm: включить `enable_kitty_graphics` в wezterm.lua или переключить лого на
   `iterm`/`sixel`?
3. Kvantum Mokka (статические цвета SVG) + цвета Noctalia KColorScheme — допустимо, т.к. палитра та же Mocha Mauve;
   при смене палитры Noctalia на другую Kvantum останется Mokka. Принять?
4. Как часто проверять апстрим: только по кнопке/команде (предлагается) или таймером systemd --user?
