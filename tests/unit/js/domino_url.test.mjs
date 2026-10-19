import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';

const dominoUrlSourcePath = new URL('../../../chat_ui/core/domino-url.js', import.meta.url);
const dominoUrlSource = await readFile(dominoUrlSourcePath, 'utf8');

async function loadDominoUrlModule() {
    const context = vm.createContext({ URL });
    const module = new vm.SourceTextModule(dominoUrlSource, {
        context,
        identifier: String(dominoUrlSourcePath),
    });
    await module.link(() => {
        throw new Error('domino-url.js should not import other modules');
    });
    await module.evaluate();
    return module.namespace;
}

test('getDominoExternalOrigin removes the App subdomain and preserves the port', async () => {
    const dominoUrl = await loadDominoUrlModule();

    assert.equal(
        dominoUrl.getDominoExternalOrigin({
            href: 'https://app.domino.example:8443/apps/cde/',
            origin: 'https://app.domino.example:8443',
        }),
        'https://domino.example:8443',
    );
});

test('getDominoExternalOrigin leaves other hostnames unchanged', async () => {
    const dominoUrl = await loadDominoUrlModule();

    assert.equal(
        dominoUrl.getDominoExternalOrigin({
            href: 'https://domino.example/apps/cde/',
            origin: 'https://domino.example',
        }),
        'https://domino.example',
    );
    assert.equal(
        dominoUrl.getDominoExternalOrigin({
            href: 'https://apps.domino.example/apps/cde/',
            origin: 'https://apps.domino.example',
        }),
        'https://apps.domino.example',
    );
});

test('getDominoExternalOrigin falls back to the unchanged current origin', async () => {
    const dominoUrl = await loadDominoUrlModule();

    assert.equal(
        dominoUrl.getDominoExternalOrigin({
            href: 'not a URL',
            origin: 'https://app.domino.example',
        }),
        'https://app.domino.example',
    );
});

test('resolveDominoExternalUrl combines the public origin with the redirect path', async () => {
    const dominoUrl = await loadDominoUrlModule();

    assert.equal(
        dominoUrl.resolveDominoExternalUrl(
            '/u/owner/project/extension?mountPointType=projectSidebar&extensionId=ext-1',
            {
                href: 'https://app.domino.example/apps/cde/',
                origin: 'https://app.domino.example',
            },
        ),
        'https://domino.example/u/owner/project/extension?mountPointType=projectSidebar&extensionId=ext-1',
    );
});
