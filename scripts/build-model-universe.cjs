const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..');
const preview = path.join(root, 'previews/overview-models-20260911');
const output = path.join(root, 'assets/model-universe');
fs.mkdirSync(output, {recursive:true});
const catalogFile = path.join(output, 'catalog.json');
if (!fs.existsSync(catalogFile)) {
  const context = {window:{}};
  vm.runInNewContext(fs.readFileSync(path.join(preview, 'data.js'), 'utf8'), context);
  fs.writeFileSync(catalogFile, JSON.stringify(context.window.PREVIEW_DATA, null, 2) + '\n');
}
const catalog = JSON.parse(fs.readFileSync(catalogFile, 'utf8'));
if (!catalog.models.length || new Set(catalog.models.map(m=>m.id)).size !== catalog.models.length) throw Error('Invalid catalog');
require(path.join(preview, 'sync-orbit.cjs'));
let html = fs.readFileSync(path.join(preview, 'overview-preview-v3.html'), 'utf8');
html = html.replace(/<title>.*?<\/title>/, '<title>SPECTRA · 模型宇宙</title>');
html = html.replace(/window.PREVIEW_DATA = [\s\S]*?;\s*<\/script>/, () => 'window.PREVIEW_DATA = ' + JSON.stringify(catalog).replace(/</g,'\\u003c') + ';\n</script>');
html = html.replace('</head>', '<style>body>.topbar,main>.masthead,main>.intro,main>.weekly,main>footer{display:none!important}main{padding:20px 24px;max-width:1400px}.orbit{height:620px}.section-head h2{font-size:22px}.explorer{display:block}</style></head>');
// Freshness is relative to today; the catalog verification date is not a release date.
html = html.replace('const isRecent=m=>m.update&&m.update<=D.checkedAt&&m.update>=new Date(new Date(`${D.checkedAt}T00:00:00Z`).getTime()-29*86400000).toISOString().slice(0,10);', 'const today=new Date().toISOString().slice(0,10);const isRecent=m=>m.update&&m.update<=today&&m.update>=new Date(Date.now()-29*86400000).toISOString().slice(0,10);');
fs.writeFileSync(path.join(output, 'index.html'), html);
console.log(`Built model universe: ${catalog.models.length} profiles`);
