# Extension Manual Install Instructions

In order to install this extension, you must enable some Central Config values, create its Domino Environment, create
the Extension's Project, configure Project environment variables, deploy an App, and promote it to be an extension

## Central Config Values

Enable Extended Identity Propagation so the viewing user's identity is attached to requests they send when interacting with apps.

Set this Central Config value:

```text
com.cerebro.domino.apps.extendedIdentityPropagationToAppsEnabled=true
```

## Domino Environment Installation

Create a Domino Environment using the [Dockerfile](./Dockerfile) named `Clinical Data Explorer`. Use the
"Required Domino Environment Base Image" in the comment in the top of the Dockerfile as the Domino Environment base
docker image. Also, set the `ARG EXTENSION_VERSION` to be your desired version. Wait for the Environment to finish
building.

## Extension Project Creation

Create a Git-Backed Project using the [clinical-data-explorer](https://github.com/dominodatalab/clinical-data-explorer)
GitHub repository. This is a public repository, so you don't need to supply credentials.

Next, you would configure Project Environment Variables. In order to configure the Chat-With-Your-Data feature, you
need to set the environment variables mentioned in the following [section](https://github.com/dominodatalab/clinical-data-explorer#optional-ai-chat-feature).
By default, the Chat feature uses OpenAI.

There are environment variables for configuring the size of in-memory dataset and chat history caching and processing.
Depending on what usage you expect from your users you may need to set some variables here. All of the following
variables have defaults.

**file downloading**
- Set `DATA_FILE_CACHE_EXPIRATION_SECONDS` to a higher number if the dataframe created from a selected file takes a long time to create.
- Set `DATA_FILE_CACHE_MAX_ITEM_COUNT` to a higher number if you have higher concurrent use of the app

**datasets processing**
- Set `DATA_FILE_SIZE_LIMIT_B` to the max file size that a user may want to use
- Set `MCP_SERVER_DATAFRAME_CACHE_SIZE_B` to 5x the size of the largest data file, and then multiply that by the number of users expected to actively use the App in an hour
- Set `DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH` to the maximum number of users that you expect to download datasets at the same time
- Set `MCP_SESSION_MAX_AGE` to a higher number if it seems like datasets are removed from the cache too quickly
- Set `MCP_SESSION_MAX_COUNT` to a higher number if you have high concurrent usage

**chat history**
- Set `CHAT_AGENT_MESSAGE_HISTORY_CACHE_SIZE_B` to configure how much chat history is kept in memory. You may want to increase this if you have higher per-hour concurrent use by users.
- Set `CHAT_AGENT_MESSAGE_HISTORY_CAP` to limit the amount of chat history that is used as context/stored in the cache to a certain number of messages

See the environment variables section in the [README.md](./README.md) for more options.

## App Deployment

Open the `Publish` modal and fill out the sections in the wizard.

**Details Section**
- Name the app `Clinical Data Explorer`
- Check the `Enable deep linking and query parameters` checkbox

**Code Section**
- Select the `Git reference` option that matches what you configured as `EXTENSION_VERSION` in the Domino Environment
- Set `./start_servers_prod.sh` as the `App file`

**Deployment Section**
- Select the `Clinical Data Explorer` Domino Environment
- Select the `Medium` Hardware Tier (works with the default cache settings). Select the Tier that provides 50-100% more memory than the `MCP_SERVER_DATAFRAME_CACHE_SIZE_B`
- Turn on autoscaling, set the minimum number of pods to be able to accomodate ~5x the largest data file a user would use multiplied by the hourly concurrency use by unique users, set the `Memory % target` to 50%

**Data Section**
- Set `Who can View`, to `Anyone in Domino`
- Check the box `Allow App to act for viewers in Domino`

Then submit the modal and wait for the App to get to the `Running` state.

## Enable Extension

Open the action menu in the `Apps & Agents` table and click the `Create Extension` button. The extension should appear
in the Project Side Bar in the `Extensions` section.




