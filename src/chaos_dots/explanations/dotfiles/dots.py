class DotfilesExplain():
    def explain_dotfiles(self, detail_level='basic'):
        return {
            'concept': 'Declarative Dotfile Management',
            'what': '"Dotfiles" are configuration files (often starting with a `.`) that customize programs. This role automates managing them by linking files from a dedicated git repository to their correct locations in your home directory.',
            'why': 'To keep all your configurations version-controlled in one or more git repositories. This makes your setup portable, reproducible, and easy to back up and sync across multiple machines.',
            'how': 'For each entry in the `dotfiles` list, the role clones the specified git repository. It then processes a list of `links` to create symlinks from the repository to your home directory, backing up any existing files. It also records its state to clean up old, unused links automatically.',
            'commands': ['git', 'ln', 'mv', 'rm'],
            'files': ['~/.dotfiles/chaos/', '~/.local/state/chaos/'],
            'examples': [
                {
                    'yaml': """dotfiles:
  - user: "dex"
    url: "https://github.com/dexmachina/dots.git"
    branch: "main"
    pull: True
    links:
      - from: "nvim"
        to: ".config/nvim"
      - from: "alacritty"
        to: ".config/alacritty"
        open: True
      - from: ".bashrc"
""",
                }
            ],
            'learn_more': ['Dotfiles on Arch Wiki', 'Dotfiles on GitHub']
        }

    def explain_open(self, detail_level='basic'):
        """Explains the 'open: true' link type"""
        return {
            'concept': 'Open Linking (open: true)',
            'what': 'An "open" link treats the source (`from`) as a folder and links all files and directories *inside* it to the destination folder (`to`).',
            'why': 'This is for managing application configs that are composed of multiple separate files within a single folder, such as `i3` or `polybar`. Instead of linking the parent folder itself, you link its individual contents.',
            'how': 'If your dotfiles repo has a folder `polybar/` containing `config.ini` and `launch.sh`, an open link from `polybar` to `.config/polybar` will result in two symlinks: `~/.config/polybar/config.ini` and `~/.config/polybar/launch.sh`.',
            'equivalent': """# Equivalent of an open link from 'polybar' to '.config/polybar'
ln -s ~/.dotfiles/chaos/dots/polybar/config.ini\\
~/.config/polybar/config.ini
ln -s ~/.dotfiles/chaos/dots/polybar/launch.sh\\
~/.config/polybar/launch.sh
""",
        }

    def explain_closed(self, detail_level='basic'):
        """Explains the default link type"""
        return {
            'concept': 'Closed Linking (Default)',
            'what': 'A "closed" link (the default when `open` is `false` or absent) creates a single, direct symlink from the source (`from`) to the destination (`to`).',
            'why': 'This is the standard and most common method, used for linking a single configuration file (like `.bashrc`) or a self-contained configuration directory (like Neovim\'s `.config/nvim`).',
            'how': 'If `from` is `nvim` and `to` is `.config/nvim`, a single symlink is created: `~/.config/nvim` -> `~/.dotfiles/chaos/dots/nvim`. If `to` is omitted, it defaults to the `from` value (e.g., `~/.bashrc` -> `~/.dotfiles/chaos/dots/.bashrc`).',
            'equivalent': """# Equivalent of a closed link from 'nvim' to '.config/nvim'
ln -s ~/.dotfiles/chaos/dots/nvim ~/.config/nvim
""",
        }

    def explain_state(self, detail_level='basic'):
        """Explains the state management and cleanup feature"""
        return {
            'concept': 'State Management and Cleanup',
            'what': 'The role saves a record of the links it manages for each repository to a state file located at `~/.local/state/chaos/dotfiles_<repo_name>`.',
            'why': 'To enable automatic and safe cleanup. When you remove a link from your Ch-aOS configuration, the role consults this state file and knows which symlinks to delete from your home directory on the next run. This prevents orphaned configuration files.',
            'how': 'After successfully applying the desired links, the role writes a list of all managed links to the state file. On subsequent runs, it compares this previous state with the new desired configuration to identify which links are now obsolete and should be removed.',
            'technical': 'This stateful approach is key to making the dotfile management declarative. You only need to define what you want, and the role handles the logic for both creation and deletion.'
        }
