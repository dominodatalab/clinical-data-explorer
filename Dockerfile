FROM python:3.13-slim-bullseye
#
# Required Domino Environment Base Image: python:3.13-slim-bullseye
#

LABEL maintainer="Domino Data Lab"
LABEL description="Clinical Data Explorer"
LABEL version="1.0.0"

ARG EXTENSION_VERSION=niole.noticket.add_dockerfile_development_quickstart_fix_dataset_bug_uv
ARG GITHUB_ORG=dominodatalab
ARG DUSER=ubuntu
ARG DGROUP=ubuntu
ARG DEBIAN_FRONTEND=noninteractive

ENV DOMINO_USER=$DUSER
ENV DOMINO_GROUP=$DGROUP
ENV HOME=/home/${DOMINO_USER}

# Set Python environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

#
# Add Domino requirements
#
RUN apt-get update && \
    # Security updates
    grep security /etc/apt/sources.list > /etc/apt/security.sources.list && \
    apt-get upgrade -y -o Dir::Etc::SourceList=/etc/apt/security.sources.list && \
    apt-get install -y \
        apt-utils \
    # add C compiler for some of the python packages required in the training job
        build-essential \
        gcc \
    # Requirements for Domino executions
        curl \
        procps \
    # Requirements for node installation
        ca-certificates \
    # For troubleshooting
        sqlite3 \
    # Requirement for extension FE deps installation
        git

#
# Add Domino user
#
RUN if ! id 12574 >/dev/null 2>&1; then \
        groupadd -g 12574 ${DOMINO_GROUP}; \
        useradd -u 12574 -g 12574 -m -N -s /bin/bash ${DOMINO_USER}; \
    fi

RUN chown -R ${DOMINO_USER}:${DOMINO_GROUP} "${HOME}"

WORKDIR ${HOME}

RUN test -n "$EXTENSION_VERSION" || (echo "EXTENSION_VERSION build arg is empty" && exit 1)
RUN git clone https://github.com/$GITHUB_ORG/clinical-data-explorer.git && cd clinical-data-explorer && git checkout $EXTENSION_VERSION
RUN chown -R ${DOMINO_USER}:${DOMINO_GROUP} "${HOME}/clinical-data-explorer"

WORKDIR ${HOME}/clinical-data-explorer

#
# Install dependencies
#

# install uv as the runtime user so it is available after the final USER switch.
# Some refs of this repo use uv/pyproject, while older refs still use
# requirements.txt + plain python startup, so install dependencies accordingly.
USER ${DOMINO_USER}
ENV PATH="${HOME}/.local/bin:${PATH}"
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
RUN uv sync --locked

# allow model endpoint builds to succeed -- seems /mnt is a python slim pre-existing dir
# and model endpoint builds create directories inside it which fails since its owned by another user
USER root
RUN chmod 777 /mnt

# Cleanup after apt package installs
RUN rm -rf /var/lib/apt/lists/*

# allow model endpoint builds to succeed -- permission errors with certain directory operations without this
USER ${DOMINO_USER}

CMD ["./start_servers.sh"]
