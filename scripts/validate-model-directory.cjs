const fs = require('fs');
const vm = require('vm');
const path = require('path');

const root = path.resolve(__dirname, '..');
const previewRoot = path.join(root, 'previews', 'overview-models-20260911');
const source = fs.readFileSync(path.join(previewRoot, 'data.js'), 'utf8');
const context = { window: {} };
vm.runInNewContext(source, context);
const data = context.window.PREVIEW_DATA;
const allowedRegions = new Set(['cn', 'global', 'research']);
const categoryIds = new Set(data.categories.map(item => item.id));
const ids = data.models.map(item => item.id);
const errors = [];

if (new Set(ids).size !== ids.length) errors.push('model ids must be unique');
for (const model of data.models) {
  if (!categoryIds.has(model.category)) errors.push(`${model.id}: invalid category`);
  if (!/^https:\/\//.test(model.source || '')) errors.push(`${model.id}: official HTTPS source required`);
  if (model.region && !allowedRegions.has(model.region)) errors.push(`${model.id}: invalid region`);
  if (!model.kind || !model.summary || !model.company) errors.push(`${model.id}: incomplete profile`);
  if (!model.currentVersion || !model.currentVersionStatus) errors.push(`${model.id}: current version status required`);
  if (!model.releaseDateStatus) errors.push(`${model.id}: release date status required`);
  if (!Array.isArray(model.access) || !model.access.length) errors.push(`${model.id}: access mode required`);
}

for (const file of ['overview-preview.html', 'overview-preview-v2.html', 'overview-preview-v3.html']) {
  const html = fs.readFileSync(path.join(previewRoot, file), 'utf8');
  const match = html.match(/window\.PREVIEW_DATA = \{[\s\S]*?\n\};/);
  if (!match) {
    errors.push(`${file}: embedded data missing`);
    continue;
  }
  const embedded = { window: {} };
  vm.runInNewContext(match[0], embedded);
  if (JSON.stringify(embedded.window.PREVIEW_DATA) !== JSON.stringify(data)) {
    errors.push(`${file}: embedded data differs from data.js`);
  }
}

const summary = {
  checkedAt: data.checkedAt,
  models: data.models.length,
  versions: data.models.reduce((sum, item) => sum + item.versions.length, 0),
  withVersions: data.models.filter(item => item.versions.length).length,
  categories: Object.fromEntries(data.categories.map(category => [
    category.id,
    data.models.filter(model => model.category === category.id).length,
  ])),
  errors,
};
console.log(JSON.stringify(summary, null, 2));
if (errors.length) process.exit(1);
