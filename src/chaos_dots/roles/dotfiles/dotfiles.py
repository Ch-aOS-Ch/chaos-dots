from io import StringIO
import yaml
import time
import os

from pyinfra.operations import server, pacman, git, files
from omegaconf import OmegaConf
from pyinfra.api.operation import add_op
from pyinfra.facts.files import Directory, File, Link
from pyinfra.facts.server import Command


def handleGit(state, host, users, sysUsers, dot):
    user = dot.get('user')
    if user not in users:
        print(f"Skipping dotfile setup for user '{user}', as they do not exist on system.")
        return None, None, None, None
    if user in sysUsers:
        print(f"Skipping dotfile setup for system user '{user}', activity not allowed.")
        return None, None, None, None
    dotName = dot.get('url').split('/')[-1].replace('.git','')
    dotLoc = f"/home/{user}/.dotfiles/chaos/{dotName}"
    dotLocEx = host.get_fact(Directory, path=dotLoc, user=user)
    branch = dot.get('branch', 'main')
    pull = dot.get('pull', False)
    if dotLocEx and pull:
        add_op(
            state,
            git.repo,
            name=f"Updating dotfile repo for user '{user}': {dot.get('url')}",
            dest=dotLoc,
            branch=branch,
            pull=pull,
            user=user,
        )
    elif not dotLocEx:
        add_op(
            state,
            git.repo,
            name=f"Cloning dotfile repo for user '{user}': {dot.get('url')}",
            src=dot.get('url'),
            dest=dotLoc,
            branch=branch,
            pull=pull,
            user=user,
        )
    return dotLoc, dotName, dot, user

def handleDotDelta(host, dotName, dot):
    user = dot.get('user')
    userHome = f"/home/{user}"
    prevRun=f"{userHome}/.local/state/chaos/dotfiles_{dotName}"
    prevRunContent = host.get_fact(File, path=prevRun)
    if prevRunContent:
        runDict = yaml.safe_load(prevRunContent)
    else:
        runDict = {"applied": []}
    applied = runDict.get('applied', [])
    links = dot.get('links', [])
    if not applied:
        return [], links

    desiredSrcs = {link.get('from', []) for link in links}

    pathToRemove = []

    for item in applied:
        src = item.get('source')
        if src not in desiredSrcs:
            if item.get('open'):
                mangd = item.get('managed_files', [])
                pathToRemove.extend(mangd)
            else:
                relativePath = item.get('path')
                if relativePath:
                    fullPath = f"{userHome}/{relativePath}"
                    pathToRemove.append(fullPath)
    return pathToRemove, links

def handleDotLogic(state, host, dotLoc, dotName, links, user, pathToRemove):

    if pathToRemove:
        for path in pathToRemove:
            pathEx = host.get_fact(File, path=path) or host.get_fact(Directory, path=path)
            if pathEx:
                add_op(
                    state,
                    files.file,
                    name=f"Removing obsolete dotfile at: {path}",
                    path=path,
                    user=user,
                    present=False,
                    _sudo=True,
                    _sudo_user=user
                )

    repoContents_raw = host.get_fact(Command, f"ls -A1 {dotLoc}", _sudo=True, _sudo_user=user)
    repoContents = repoContents_raw.strip().splitlines() if repoContents_raw else []
    newRun = []

    for link in links:
        src = link.get('from')
        if src not in repoContents:
            print(f"Warning: Source path '{src}' does not exist in dotfiles for user '{user}', skipping link creation.")
            continue
        linkFrom = link.get('from')
        destRel = link.get('to') or linkFrom
        parentDir = os.path.dirname(destRel)
        if parentDir:
            fullParentPath = f"/home/{user}/{parentDir}"
            add_op(
                state,
                files.directory,
                name=f"Ensuring parent directory exists: {fullParentPath}",
                path=fullParentPath,
                user=user,
                mode='0755',
                present=True,
                _sudo=True,
                _sudo_user=user
            )
        if link.get('open') == True:
            openMngd = manageOpen(state, host, dotLoc, linkFrom, destRel, user)
            newRun.append({'source': linkFrom, 'path': destRel, 'open': True, 'managed_files': openMngd})
        else:
            manageClosed(state, host, dotLoc, linkFrom, destRel, user)
            newRun.append({'source': linkFrom, 'path': destRel, 'open': False, 'managed_files': []})

    stateDir = f"/home/{user}/.local/state/chaos"
    stateFile = f"{stateDir}/dotfiles_{dotName}"

    conf = OmegaConf.create({'applied': newRun})
    yamlContent = OmegaConf.to_yaml(conf)
    add_op(
        state,
        files.directory,
        name=f"Ensuring state directory exists: {stateDir}",
        path=stateDir,
        user=user,
        mode='0755',
        present=True,
        _sudo=True,
        _sudo_user=user
    )

    add_op(
        state,
        files.put,
        name= f"Recording applied dotfile state to: {stateFile}",
        src=StringIO(yamlContent),
        dest=stateFile,
        mode='0644',
        user=user,
        _sudo=True,
        _sudo_user=user
    )

def manageClosed(state, host, dotLoc, linkFrom, destRel, user):
    target = f"/home/{user}/{destRel}"
    srcItem = f"{dotLoc}/{linkFrom}"
    currentLink = host.get_fact(Link, path=target)
    exists = (currentLink is not None) or host.get_fact(File, path=target) or host.get_fact(Directory, path=target)
    needsBackup = False
    if exists:
        if currentLink is None:
            needsBackup = True
        elif currentLink != srcItem:
            needsBackup = True
    if needsBackup:
        timestamp = int(time.time())
        add_op(
            state,
            server.shell,
            name=f"Backing up existing file before creating link: {target}",
            commands=f"mv {target} {target}.bak_{timestamp}",
            _sudo=True,
            _sudo_user=user
        )
    add_op(
        state,
        files.link,
        name=f"Creating link from {srcItem} to {target}",
        path=target,
        target=srcItem,
        symbolic=True,
        user=user,
        create_remote_dir=True,
        _sudo=True,
        _sudo_user=user
    )


def manageOpen(state, host, dotLoc, linkFrom, destRel, user):
    openFiles_raw = host.get_fact(Command, f"ls -A1 {dotLoc}/{linkFrom}", _sudo=True, _sudo_user=user)
    openFiles = openFiles_raw.strip().splitlines() if openFiles_raw else []
    targetPath = f"/home/{user}/{destRel}"
    openMngd = []
    for item in openFiles:
        target = f"{targetPath}/{item}"

        srcItem = f"{dotLoc}/{linkFrom}/{item}"
        currentLink = host.get_fact(Link, path=target)
        exists = (currentLink is not None) or host.get_fact(File, path=target) or host.get_fact(Directory, path=target)

        needsBackup = False
        if exists:
            if currentLink is None:
                needsBackup = True
            elif currentLink != srcItem:
                needsBackup = True

        if needsBackup:
            timestamp = int(time.time())
            add_op(
                state,
                server.shell,
                name=f"Backing up existing file before creating open link: {target}",
                commands=f"mv {target} {target}.bak_{timestamp}",
                _sudo=True,
                _sudo_user=user
            )
        add_op(
            state,
            files.link,
            name=f"Creating open link from {srcItem} to {target}",
            path=target,
            target=srcItem,
            symbolic=True,
            user=user,
            create_remote_dir=True,
            _sudo=True,
            _sudo_user=user
        )
        openMngd.append(target)
    return openMngd

def run_dotfiles(state, host, chobolo_path, skip):
    sysDeps = ["git"]
    add_op(
        state,
        pacman.packages,
        name="Installing system dependencies.",
        packages=sysDeps,
        present=True,
        _sudo=True
    )
    users_raw_str = host.get_fact(Command, "awk -F: '($3>=1000 && $7 ~ /(bash|zsh|fish|sh)$/){print $1}' /etc/passwd")
    users_raw = users_raw_str.strip().splitlines() if users_raw_str else []
    users = set(users_raw) - {'nobody'}
    ChObolo = OmegaConf.load(chobolo_path)
    sysUsers = host.get_fact(Command, "awk -F: '($3<1000){print $1}' /etc/passwd").strip().splitlines() if users_raw_str else []

    for dots in ChObolo.get('dotfiles', []):
        dotLoc, dotName, dot, user = handleGit(state, host, users, sysUsers, dots)
        if not dotLoc:
            continue
        pathToRemove, links = handleDotDelta(host, dotName, dot)
        print(f"Processing dotfiles for user '{user}': {dotName}")
        print(f"Links to process: {links}")
        print(f"Paths to remove: {pathToRemove}")
        confirm = "y" if skip else input("\nIs This correct (Y/n)? ")
        if confirm.lower() in ["y", "yes", "", "s", "sim"]:
            handleDotLogic(state, host, dotLoc, dotName, links, user, pathToRemove)
