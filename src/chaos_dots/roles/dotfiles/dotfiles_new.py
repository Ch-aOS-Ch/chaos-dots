from io import StringIO
import yaml
import time
import os

from pyinfra.operations import server, pacman, git, files
from omegaconf import OmegaConf
from pyinfra.api.operation import add_op
from pyinfra.api.operations import run_ops
from pyinfra.facts.files import Directory, File
from pyinfra.facts.server import Command

def getFilesystemState(host, user, paths):
    if not paths:
        return {}

    pathArgs = " ".join([f"'{p}'" for p in paths])
    command = f"find {pathArgs} -maxdepth 0 -printf '%p\t%Y\t%l\n' 2>/dev/null || true"
    rawOutput = host.get_fact(Command, command, _sudo=True, _sudo_user=user)

    fsState = {}
    if rawOutput:
        for line in rawOutput.strip().splitlines():
            parts = line.split('\t')
            path = parts[0]
            fileType = parts[1]
            linkTarget = parts[2] if len(parts) > 2 else ""
            fsState[path] = {
                "path": path,
                "is_link": fileType == 'l',
                "is_file": fileType == 'f',
                "is_dir": fileType == 'd',
                "link_target": linkTarget if fileType == 'l' else None,
                "exists": True
            }
    return fsState


def handleGitRepo(state, host, users, sysUsers, dot):
    user = dot.get('user')
    if user not in users:
        print(f"Skipping dotfile setup for user '{user}', as they do not exist on system.")
        return None, None, None, None
    if user in sysUsers:
        print(f"Skipping dotfile setup for system user '{user}', activity not allowed.")
        return None, None, None, None

    dotName = dot.get('url').split('/')[-1].replace('.git', '')
    dotLoc = f"/home/{user}/.dotfiles/chaos/{dotName}"
    dotLocEx = host.get_fact(Directory, path=dotLoc)

    branch = dot.get('branch', 'main')
    pull = dot.get('pull', False)
    should_run_git = not dotLocEx or pull or (dotLocEx and branch)
    if should_run_git:
        add_op(
            state, git.repo,
            name=f"Ensuring dotfile repo state for '{user}'",
            src=dot.get('url'),
            dest=dotLoc,
            branch=branch,
            pull=pull,
            user=user,
        )
    return dotLoc, dotName, dot, user


def manageSingleLink(state, user, sourceItem, targetPath, fsState):
    targetState = fsState.get(targetPath)

    if targetState and targetState.get("exists") and \
       (not targetState.get("is_link") or targetState.get("link_target") != sourceItem):

        timestamp = int(time.time())
        backupPath = f"{targetPath}.bak_{timestamp}"
        parentDir = os.path.dirname(targetPath)

        commands = [
            f"mv '{targetPath}' '{backupPath}'",
            f"mkdir -p '{parentDir}'",
            f"ln -sfn '{sourceItem}' '{targetPath}'"
        ]

        add_op(
            state,
            server.shell,
            name=f"Backing up existing file and creating link: {targetPath}",
            commands=commands,
            _sudo=True,
            _sudo_user=user,
        )
    elif not (targetState and targetState.get("exists")):
        parentDir = os.path.dirname(targetPath)
        add_op(
            state, files.directory,
            name=f"Ensuring parent directory exists: {parentDir}",
            path=parentDir, user=user, present=True, _sudo=True, _sudo_user=user
        )
        add_op(
            state, files.link,
            name=f"Creating link: {targetPath} -> {sourceItem}",
            path=targetPath, target=sourceItem, user=user, _sudo=True, _sudo_user=user
        )


def runDotfiles(state, host, choboloPath, skip):
    sysDeps = ["git", "findutils"]
    add_op(
        state, pacman.packages, name="Installing system dependencies.",
        packages=sysDeps, present=True, _sudo=True
    )

    usersRawStr = host.get_fact(Command, "awk -F: '($3>=1000 && $7 ~ /(bash|zsh|fish|sh)$/){print $1}' /etc/passwd")
    users = set(usersRawStr.strip().splitlines() if usersRawStr else []) - {'nobody'}
    chObolo = OmegaConf.load(choboloPath)
    sysUsersRaw = host.get_fact(Command, "awk -F: '($3<1000){print $1}' /etc/passwd")
    sysUsers = set(sysUsersRaw.strip().splitlines() if sysUsersRaw else [])

    if not chObolo.get('dotfiles'):
        print(f"\nNo dotfiles configured, skipping dotfile setup.")

    for dotConfig in chObolo.get('dotfiles', []):
        dotLoc, dotName, dot, user = handleGitRepo(state, host, users, sysUsers, dotConfig)
        if not dotLoc:
            continue

        userHome = f"/home/{user}"
        desiredLinks = dot.get('links', [])

        prevStateFile = f"{userHome}/.local/state/charonte/dotfiles_{dotName}"
        prevStateContent = host.get_fact(Command, f"cat {prevStateFile} || true", _sudo=True, _sudo_user=user)
        prevState = yaml.safe_load(prevStateContent) if prevStateContent else {"applied": []}

        pathsToRemove = []
        desiredSources = {link.get('from') for link in desiredLinks}
        for item in prevState.get('applied', []):
            if item.get('source') not in desiredSources:
                if item.get('open'):
                    pathsToRemove.extend(item.get('managed_files', []))
                elif item.get('path'):
                    pathsToRemove.append(f"{userHome}/{item.get('path')}")
        dotLocExists = host.get_fact(Directory, path=dotLoc)
        if not dotLocExists:
            print(f"Info: Dotfile repo for '{dotName}' is being cloned. Links will be processed on the next run.")
            continue

        repoContentsRaw = host.get_fact(Command, f"ls -A1 {dotLoc}", _sudo=True, _sudo_user=user)
        repoContents = set(repoContentsRaw.strip().splitlines() if repoContentsRaw else [])

        pathsToStat = set(pathsToRemove)
        openLinkSourceFiles = {}

        for link in desiredLinks:
            source = link.get('from')
            if source not in repoContents:
                continue

            destRel = link.get('to') or source

            if link.get('open'):
                targetBaseDir = f"{userHome}/{destRel}"
                sourceDir = f"{dotLoc}/{source}"
                openFilesRaw = host.get_fact(Command, f"ls -A1 {sourceDir} 2>/dev/null || true", _sudo=True, _sudo_user=user)
                openFiles = openFilesRaw.strip().splitlines() if openFilesRaw else []
                openLinkSourceFiles[source] = openFiles
                for item in openFiles:
                    pathsToStat.add(os.path.normpath(f"{targetBaseDir}/{item}"))
            else:
                link_path = source if destRel == '.' else destRel
                pathsToStat.add(f"{userHome}/{link_path}")

        fsState = getFilesystemState(host, user, list(pathsToStat))

        print(f"\nProcessing dotfiles for user '{user}': {dotName}")
        print(f"Links to process: {desiredLinks}")
        print(f"Paths to remove: {pathsToRemove}")
        confirm = "y" if skip else input("Is This correct (Y/n)? ")

        if confirm.lower() in ["y", "yes", "", "s", "sim"]:
            for path in pathsToRemove:
                if fsState.get(path, {}).get('exists'):
                    add_op(
                        state,
                        server.shell,
                        name=f"Removing obsolete path: {path}",
                        commands=[f"rm -rf '{path}'"],
                        _sudo=True,
                        _sudo_user=user,
                    )

            newRunState = []
            for link in desiredLinks:
                source = link.get('from')
                if source not in repoContents:
                    print(f"Warning: Source path '{source}' not in repo, skipping.")
                    continue

                destRel = link.get('to') or source

                if link.get('open'):
                    managedFiles = []
                    targetBaseDir = f"{userHome}/{destRel}"
                    sourceFiles = openLinkSourceFiles.get(source, [])

                    for item in sourceFiles:
                        sourceItem = f"{dotLoc}/{source}/{item}"
                        targetPath = os.path.normpath(f"{targetBaseDir}/{item}")
                        manageSingleLink(state, user, sourceItem, targetPath, fsState)

                        managedFiles.append(targetPath)

                    newRunState.append({'source': source, 'path': destRel, 'open': True, 'managed_files': managedFiles})
                else:
                    sourceItem = f"{dotLoc}/{source}"
                    link_path = source if destRel == '.' else destRel
                    targetPath = f"{userHome}/{link_path}"
                    manageSingleLink(state, user, sourceItem, targetPath, fsState)

                    newRunState.append({'source': source, 'path': link_path, 'open': False, 'managed_files': []})

            stateDir = f"{userHome}/.local/state/charonte"
            stateFile = f"{stateDir}/dotfiles_{dotName}"
            conf = OmegaConf.create({'applied': newRunState})
            yamlContent = OmegaConf.to_yaml(conf)

            add_op(
                state, files.directory, name=f"Ensuring state directory exists: {stateDir}",
                path=stateDir, user=user, present=True, _sudo=True, _sudo_user=user
            )
            add_op(
                state, files.put, name=f"Recording applied dotfile state to: {stateFile}",
                src=StringIO(yamlContent), dest=stateFile, user=user, _sudo=True, _sudo_user=user
            )
