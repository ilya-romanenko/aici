#!/usr/bin/env node
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';
import crypto from 'node:crypto';
import { performance } from 'node:perf_hooks';

import browserslist from 'browserslist';
import esbuild from 'esbuild';
import fg from 'fast-glob';
import fsExtra from 'fs-extra';
import { minify as minifyHtml } from 'html-minifier-terser';
import sharp from 'sharp';
import { browserslistToTargets, transform as transformCss } from 'lightningcss';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = path.resolve(__dirname, '..');
const FRONTEND_ROOT = path.join(ROOT_DIR, 'src', 'ai_crypto_index', 'frontend');
const STATIC_SRC = path.join(FRONTEND_ROOT, 'static');
const TEMPLATES_SRC = path.join(FRONTEND_ROOT, 'templates');
const DIST_ROOT = path.join(ROOT_DIR, 'dist');
const DIST_STATIC = path.join(DIST_ROOT, 'static');
const DIST_TEMPLATES = path.join(DIST_ROOT, 'templates');
const WATCH_MODE = process.argv.includes('--watch');
const CSS_TARGETS = browserslistToTargets(browserslist(['defaults', 'node 18']));

const msg = (...parts) => {
  const timestamp = new Date().toISOString().split('T')[1].replace('Z', '');
  console.log(`[build ${timestamp}]`, ...parts);
};

const normalizePath = (absolutePath) =>
  path.relative(DIST_ROOT, absolutePath).split(path.sep).join('/');

const manifest = {};

async function recordManifest(outputPath, hashBuffer = null) {
  const stats = await fsExtra.stat(outputPath);
  const relative = normalizePath(outputPath);
  manifest[relative] = {
    bytes: stats.size,
    hash: hashBuffer ? hashBuffer.toString('hex') : undefined,
  };
}

async function buildCss() {
  const cssFiles = await fg('css/**/*.css', { cwd: STATIC_SRC, onlyFiles: true });
  await Promise.all(
    cssFiles.map(async (relativePath) => {
      const sourcePath = path.join(STATIC_SRC, relativePath);
      const code = await fsExtra.readFile(sourcePath);
      const result = transformCss({
        filename: sourcePath,
        code,
        minify: true,
        sourceMap: false,
        targets: CSS_TARGETS,
      });
      const outputPath = path.join(DIST_STATIC, relativePath);
      await fsExtra.ensureDir(path.dirname(outputPath));
      await fsExtra.writeFile(outputPath, result.code);
      const hash = crypto.createHash('sha256').update(result.code).digest();
      await recordManifest(outputPath, hash);
    }),
  );
}

async function buildJs() {
  const entryPoints = await fg('js/**/*.js', { cwd: STATIC_SRC, onlyFiles: true });
  if (!entryPoints.length) {
    return;
  }

  await Promise.all(
    entryPoints.map(async (relativeEntry) => {
      const absoluteEntry = path.join(STATIC_SRC, relativeEntry);
      const result = await esbuild.build({
        entryPoints: [absoluteEntry],
        bundle: false,
        minify: true,
        sourcemap: false,
        write: false,
        format: 'iife',
        target: ['es2019'],
        platform: 'browser',
        logLevel: 'silent',
      });

      if (!result.outputFiles.length) {
        throw new Error(`Failed to generate output for ${relativeEntry}`);
      }
      const outputFile = result.outputFiles[0];
      const outputPath = path.join(DIST_STATIC, relativeEntry);
      await fsExtra.ensureDir(path.dirname(outputPath));
      await fsExtra.writeFile(outputPath, outputFile.contents);
      const hash = crypto.createHash('sha256').update(outputFile.contents).digest();
      await recordManifest(outputPath, hash);
    }),
  );
}

async function copyStaticAssets() {
  const staticFiles = await fg('**/*', {
    cwd: STATIC_SRC,
    onlyFiles: true,
    dot: false,
  });

  const processedPrefixes = new Set(['css/', 'js/']);

  for (const relativePath of staticFiles) {
    if ([...processedPrefixes].some((prefix) => relativePath.startsWith(prefix))) {
      continue;
    }
    const sourcePath = path.join(STATIC_SRC, relativePath);
    const outputPath = path.join(DIST_STATIC, relativePath);
    await fsExtra.ensureDir(path.dirname(outputPath));
    await fsExtra.copyFile(sourcePath, outputPath);
    const hash = crypto.createHash('sha256').update(await fsExtra.readFile(outputPath)).digest();
    await recordManifest(outputPath, hash);

    if (/\.(png|jpe?g)$/i.test(relativePath)) {
      const webpPath = outputPath.replace(/\.(png|jpe?g)$/i, '.webp');
      await sharp(sourcePath).webp({ quality: 82 }).toFile(webpPath);
      const webpData = await fsExtra.readFile(webpPath);
      const webpHash = crypto.createHash('sha256').update(webpData).digest();
      await recordManifest(webpPath, webpHash);
    }
  }
}

async function buildTemplates() {
  const templateFiles = await fg('**/*.html', { cwd: TEMPLATES_SRC, onlyFiles: true });
  const minifyOptions = {
    collapseWhitespace: true,
    conservativeCollapse: true,
    removeComments: true,
    removeRedundantAttributes: true,
    removeScriptTypeAttributes: true,
    removeStyleLinkTypeAttributes: true,
    keepClosingSlash: true,
    sortAttributes: false,
    sortClassName: true,
    caseSensitive: true,
    minifyCSS: false,
    minifyJS: false,
    ignoreCustomFragments: [/\{\%[\s\S]*?\%\}/g, /\{\{[\s\S]*?\}\}/g, /\{\#[\s\S]*?\#\}/g],
  };

  await Promise.all(
    templateFiles.map(async (relativePath) => {
      const sourcePath = path.join(TEMPLATES_SRC, relativePath);
      const raw = await fsExtra.readFile(sourcePath, 'utf8');
      const minified = await minifyHtml(raw, minifyOptions);
      const outputPath = path.join(DIST_TEMPLATES, relativePath);
      await fsExtra.ensureDir(path.dirname(outputPath));
      await fsExtra.writeFile(outputPath, minified);
      const hash = crypto.createHash('sha256').update(minified).digest();
      await recordManifest(outputPath, hash);
    }),
  );
}

async function writeManifest() {
  const manifestPath = path.join(DIST_ROOT, 'asset-manifest.json');
  await fsExtra.writeJson(manifestPath, manifest, { spaces: 2 });
}

async function buildOnce() {
  const start = performance.now();
  manifest.clear?.();
  Object.keys(manifest).forEach((key) => delete manifest[key]);

  await fsExtra.remove(DIST_ROOT);
  await fsExtra.ensureDir(DIST_STATIC);
  await fsExtra.ensureDir(DIST_TEMPLATES);

  await Promise.all([buildCss(), buildJs(), copyStaticAssets(), buildTemplates()]);
  await writeManifest();
  const end = performance.now();
  msg(`Build completed in ${Math.round(end - start)}ms`);
}

async function ensureWatch() {
  await buildOnce();
  msg('Entering watch mode… Press Ctrl+C to exit.');

  let isBuilding = false;
  let queued = false;
  const debounceDelay = 150;
  let timer = null;

  const scheduleBuild = () => {
    if (timer) {
      clearTimeout(timer);
    }
    timer = setTimeout(async () => {
      if (isBuilding) {
        queued = true;
        return;
      }
      isBuilding = true;
      try {
        await buildOnce();
      } catch (error) {
        msg('Build failed:', error);
      } finally {
        isBuilding = false;
        if (queued) {
          queued = false;
          scheduleBuild();
        }
      }
    }, debounceDelay);
  };

  const watchDirs = [STATIC_SRC, TEMPLATES_SRC];
  const watchOptions = process.platform === 'win32' ? { recursive: true } : {};
  watchDirs.forEach((dir) => {
    const watcher = fs.watch(dir, watchOptions, (event, filename) => {
      if (!filename) {
        return;
      }
      msg(`Change detected in ${path.join(dir, filename)}`);
      scheduleBuild();
    });
    watcher.on('error', (error) => {
      msg(`Watcher error for ${dir}:`, error.message);
    });
  });
}

try {
  if (WATCH_MODE) {
    await ensureWatch();
  } else {
    await buildOnce();
  }
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
