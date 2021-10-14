#! /usr/bin/env python3
"""Bring the docker-compose stack up"""

import argparse
import logging
import os
import shlex
import sys
import typing
from subprocess import check_output

logger = logging.getLogger(__name__)

_IMAGE_NAME = "kartoza/ckanext-dalrrd-emc-dcpr"
_FALLBACK_GIT_BRANCH = "main"
_COMPOSE_PROJECT_NAME = "emc-dcpr"
_COMPOSE_FILE_NAME = "docker-compose.dev.yml"


def _get_image_tag_name() -> typing.Optional[str]:
    current_git_branch = check_output(
        shlex.split("git rev-parse --abbrev-ref HEAD"), text=True
    ).strip("\n")
    existing_image_tags = check_output(
        shlex.split(f"docker images {_IMAGE_NAME} --format '{{{{.Tag}}}}'"), text=True
    ).split("\n")
    if current_git_branch in existing_image_tags:
        logger.debug("The current branch already has a built tag, lets use that")
        result = current_git_branch
    elif "main" in existing_image_tags:
        logger.debug(
            f"The current branch does not have a built tag yet, lets use the "
            f"{_FALLBACK_GIT_BRANCH!r} image tag"
        )
        result = _FALLBACK_GIT_BRANCH
    else:
        result = None
    return result


def _get_exec_environment(image_tag: str) -> typing.Dict[str, str]:
    env = os.environ.copy()
    env["CKAN_IMAGE_TAG"] = image_tag
    return env


def run_compose_up(args):
    image_tag = _get_image_tag_name()
    if image_tag is not None:
        exec_env = _get_exec_environment(image_tag)
        logger.info(f"Using {image_tag!r} as the tag for the CKAN image...")
        _run_docker_compose("up --detach", exec_env)
    else:
        raise SystemExit(
            f"There is no docker image for the current git branch yet, and neither "
            f"for the {_FALLBACK_GIT_BRANCH!r} branch - Please build the image "
            f"first. Aborting..."
        )


def run_compose_down(args):
    image_tag = _get_image_tag_name()
    if image_tag is not None:
        exec_env = _get_exec_environment(image_tag)
    else:
        exec_env = os.environ.copy()
    _run_docker_compose("down", exec_env)


def run_compose_restart(args):
    _run_docker_compose(f"restart {' '.join(args.service)}")


def _get_compose_command(fragment: str) -> str:
    template = (
        "docker-compose " "--project-name={project} " "--file={file_} " "{fragment}"
    )
    return template.format(
        project=_COMPOSE_PROJECT_NAME, file_=_COMPOSE_FILE_NAME, fragment=fragment
    )


def _run_docker_compose(
    command_fragment: str, environment: typing.Optional[typing.Dict[str, str]] = None
):
    env = environment or os.environ.copy()
    command = _get_compose_command(command_fragment)
    logger.debug(
        f"About to replace the current process with the one that results from running "
        f"{command!r} with an environment of {env}"
    )
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvpe("docker-compose", shlex.split(command), env)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers()
    compose_up_parser = subparsers.add_parser("up")
    compose_up_parser.set_defaults(func=run_compose_up)
    compose_down_parser = subparsers.add_parser("down")
    compose_down_parser.set_defaults(func=run_compose_down)
    compose_restart_parser = subparsers.add_parser("restart")
    compose_restart_parser.set_defaults(func=run_compose_restart)
    compose_restart_parser.add_argument("service", nargs="+")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    args.func(args)


if __name__ == "__main__":
    main()
