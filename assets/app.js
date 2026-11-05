'use strict';

(() => {
  const SVGOV = (window.SVGOV = window.SVGOV || {});
  const $ = (id) => document.getElementById(id);

  const state = {
    ready: false,
    folder: '',
    pairs: [],
    selected: null,
    busy: false,
    preview: null,
  };

  let apiReady = false;
  const bridge = () => window.pywebview.api;

  /* —— 启动 ————————————————————————————————————— */

  function boot() {
    if (apiReady) return;
    if (!window.pywebview || !window.pywebview.api) {
      window.addEventListener('pywebviewready', boot);
      return;
    }
    apiReady = true;
    init();
  }

  async function init() {
    bindUI();
    try {
      const info = await bridge().init();
      state.ready = true;
      SVGOV.ready = true;
      if (info && info.version) $('caption-version').textContent = 'v' + info.version;
      log('就绪：把文件夹拖入窗口，或点击「打开文件夹…」');
      setStatus('就绪');
      if (info && info.folder) await loadFolder(info.folder);
    } catch (err) {
      setStatus('初始化失败：' + err);
    }
  }

  /* —— 文件夹载入 ——————————————————————————————— */

  async function loadFolder(path) {
    setStatus('正在扫描 ' + path);
    const result = await bridge().scan_folder(path);
    applyScan(result);
  }

  function applyScan(result) {
    if (!result || result.error) {
      const message = (result && result.error) || '扫描失败';
      setStatus(message);
      log(message);
      return;
    }
    state.folder = result.folder;
    state.pairs = result.pairs || [];
    $('folder-path').textContent = result.folder;
    renderRows();
    updateStats(result.stats || {});
    $('btn-generate').disabled = state.busy || state.pairs.length === 0;

    if (state.pairs.length) {
      selectPair(state.pairs[0].stem);
      setStatus('已载入 ' + state.pairs.length + ' 对有效数据');
      log('扫描 ' + result.folder + '：有效 ' + state.pairs.length + ' 对');
    } else {
      clearPreview();
      setStatus('没有有效数据：图片必须存在同名 SVG');
      log('扫描 ' + result.folder + '：没有找到「图片 + 同名 SVG」的数据对');
    }
  }

  function renderRows() {
    const rows = $('rows');
    rows.textContent = '';
    for (const pair of state.pairs) {
      const tr = document.createElement('tr');
      tr.dataset.stem = pair.stem;

      const nameCell = document.createElement('td');
      nameCell.textContent = pair.imageName;
      nameCell.title = pair.imageName + '  ↔  ' + pair.svgName;

      const formatCell = document.createElement('td');
      formatCell.textContent = pair.format || '';

      const sizeCell = document.createElement('td');
      sizeCell.textContent = pair.size || '';

      const outCell = document.createElement('td');
      outCell.className = 'out-cell';
      if (pair.outputExists) {
        const badge = document.createElement('span');
        badge.className = 'badge';
        badge.textContent = '已有';
        outCell.appendChild(badge);
      } else {
        outCell.textContent = '—';
      }

      tr.append(nameCell, formatCell, sizeCell, outCell);
      rows.appendChild(tr);
    }
    $('grid').hidden = state.pairs.length === 0;
    $('list-empty').hidden = state.pairs.length > 0;
  }

  function updateStats(stats) {
    const pairs = stats.pairs || 0;
    $('status-mid').textContent = pairs + ' 对';
    $('list-hint').textContent =
      '有效 ' + pairs + ' 对 / 图片 ' + (stats.images || 0) + ' 张 / 缺 SVG ' +
      (stats.missing_svg || 0) + ' 张';
  }

  /* —— 预览 ————————————————————————————————————— */

  async function selectPair(stem) {
    state.selected = stem;
    for (const tr of $('rows').children) {
      tr.classList.toggle('selected', tr.dataset.stem === stem);
    }
    const row = findRow(stem);
    if (row) row.scrollIntoView({ block: 'nearest' });

    const payload = await bridge().preview(stem);
    if (!payload || payload.error) {
      const message = (payload && payload.error) || '预览失败';
      setStatus(message);
      log(message);
      return;
    }
    showPreview(payload);
  }

  function showPreview(payload) {
    state.preview = payload;
    $('img-base').src = payload.imageUrl;
    $('img-svg').src = payload.svgUrl;
    $('img-svg').hidden = !$('show-overlay').checked;
    $('frame').hidden = false;
    $('preview-empty').hidden = true;
    $('info-size').textContent = payload.width + ' × ' + payload.height + ' px';
    $('info-out').textContent =
      payload.outWidth + ' × ' + payload.outHeight + ' px（' + payload.scale + '×）';
    $('info-name').textContent =
      payload.outputName + (payload.outputExists ? '（已存在，将覆盖）' : '');
    requestAnimationFrame(fitFrame);
  }

  function clearPreview() {
    state.preview = null;
    state.selected = null;
    $('frame').hidden = true;
    $('preview-empty').hidden = false;
    $('img-base').removeAttribute('src');
    $('img-svg').removeAttribute('src');
    $('info-size').textContent = '—';
    $('info-out').textContent = '—';
    $('info-name').textContent = '—';
  }

  function fitFrame() {
    const preview = state.preview;
    if (!preview) return;
    const stage = $('preview-stage');
    const availableWidth = stage.clientWidth - 16;
    const availableHeight = stage.clientHeight - 16;
    if (availableWidth <= 0 || availableHeight <= 0) return;
    const ratio = Math.min(availableWidth / preview.width, availableHeight / preview.height);
    const frame = $('frame');
    frame.style.width = Math.max(1, Math.floor(preview.width * ratio)) + 'px';
    frame.style.height = Math.max(1, Math.floor(preview.height * ratio)) + 'px';
  }

  function findRow(stem) {
    return Array.prototype.find.call(
      $('rows').children,
      (tr) => tr.dataset.stem === stem
    ) || null;
  }

  /* —— 生成 ————————————————————————————————————— */

  async function generate() {
    if (state.busy) return;
    const result = await bridge().generate($('output-subdir').checked);
    if (!result || result.error) {
      const message = (result && result.error) || '无法开始生成';
      setStatus(message);
      log(message);
      return;
    }
    state.busy = true;
    setBusy(true);
    $('progress-fill').style.width = '0%';
    $('progress-text').textContent = '0 / ' + result.total;
    log('开始生成 ' + result.total + ' 个文件（3 倍分辨率 · JPEG 品质 95% · 输出到「' +
      (result.subdir ? '叠加图/' : './') + '」）');
  }

  async function cancel() {
    await bridge().cancel();
  }

  function setBusy(busy) {
    $('btn-choose').disabled = busy;
    $('btn-generate').disabled = busy || state.pairs.length === 0;
    $('btn-cancel').disabled = !busy;
  }

  /* —— Python → JS 推送 ————————————————————————— */

  SVGOV.onScanResult = (result) => {
    applyScan(result);
  };

  SVGOV.onProgress = (data) => {
    const total = data.total || 0;
    const done = data.state === '生成中' ? data.index - 1 : data.index;
    const percent = total ? Math.min(100, Math.round((done * 100) / total)) : 0;
    $('progress-fill').style.width = percent + '%';
    $('progress-text').textContent = data.index + ' / ' + total;
    setStatus(data.state + '：' + data.stem);

    const row = findRow(data.stem);
    if (row) {
      const cell = row.querySelector('.out-cell');
      if (cell) {
        if (data.state === '已生成') {
          cell.textContent = '';
          const badge = document.createElement('span');
          badge.className = 'badge';
          badge.textContent = '完成';
          cell.appendChild(badge);
        } else if (data.state === '失败') {
          cell.textContent = '失败';
        } else {
          cell.textContent = '生成中…';
        }
      }
      row.scrollIntoView({ block: 'nearest' });
    }
  };

  SVGOV.onDone = (data) => {
    state.busy = false;
    setBusy(false);
    const summary = data.cancelled
      ? '已取消：完成 ' + data.written + ' / ' + data.total + '，失败 ' + data.failed
      : '完成：成功 ' + data.written + '，失败 ' + data.failed + '（' + data.elapsed + ' 秒）';
    setStatus(summary);
    log(summary);
    $('progress-text').textContent =
      (data.cancelled ? data.written : data.total) + ' / ' + data.total;
    if (!data.cancelled) $('progress-fill').style.width = '100%';
    if (state.selected) {
      bridge().preview(state.selected).then((payload) => {
        if (payload && !payload.error) showPreview(payload);
      });
    }
  };

  SVGOV.onLog = (data) => {
    if (data && data.text) log(data.text);
  };

  /* —— 浏览器内合成（Python 调用） ————————————————— */

  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error('图片解码失败'));
      image.src = url;
    });
  }

  SVGOV.renderAndEncode = (payload, token) => {
    (async () => {
      let canvas = null;
      try {
        const [base, overlay] = await Promise.all([
          loadImage(payload.imageUrl),
          loadImage(payload.svgUrl),
        ]);

        canvas = document.createElement('canvas');
        canvas.width = payload.outWidth;
        canvas.height = payload.outHeight;

        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(base, 0, 0, canvas.width, canvas.height);
        ctx.drawImage(overlay, 0, 0, canvas.width, canvas.height);

        const dataUrl = canvas.toDataURL('image/jpeg', payload.quality || 0.95);
        await bridge().render_result(token, dataUrl, null);
      } catch (err) {
        const message = (err && err.message) ? err.message : String(err);
        try {
          await bridge().render_result(token, null, message);
        } catch (ignored) {
          /* 窗口已关闭 */
        }
      } finally {
        if (canvas) {
          canvas.width = 0;
          canvas.height = 0;
        }
      }
    })();
  };

  /* —— 界面绑定 ————————————————————————————————— */

  function bindUI() {
    $('btn-choose').addEventListener('click', chooseFolder);
    $('btn-drop').addEventListener('click', showDropHint);
    $('btn-generate').addEventListener('click', generate);
    $('btn-cancel').addEventListener('click', cancel);
    $('btn-open').addEventListener('click', openOutput);

    $('rows').addEventListener('click', (event) => {
      const row = event.target.closest('tr');
      if (!row || state.busy) return;
      selectPair(row.dataset.stem);
    });

    $('show-overlay').addEventListener('change', (event) => {
      $('img-svg').hidden = !event.target.checked;
    });

    document.querySelectorAll('.menu-title').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        toggleMenu(button.dataset.menu);
      });
    });
    document.querySelectorAll('.menu-popup button').forEach((button) => {
      button.addEventListener('click', () => {
        closeMenus();
        runCommand(button.dataset.cmd);
      });
    });
    document.addEventListener('click', closeMenus);

    $('dialog-close').addEventListener('click', closeDialog);
    $('modal').addEventListener('click', (event) => {
      if (event.target === $('modal')) closeDialog();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      closeMenus();
      closeDialog();
    });

    if (window.ResizeObserver) {
      new ResizeObserver(fitFrame).observe($('preview-stage'));
    } else {
      window.addEventListener('resize', fitFrame);
    }
    window.addEventListener('resize', fitFrame);
  }

  async function chooseFolder() {
    if (state.busy) return;
    setStatus('请选择图片所在文件夹…');
    const result = await bridge().choose_folder();
    if (!result || result.cancelled) {
      setStatus(state.folder ? '已取消' : '就绪');
      return;
    }
    applyScan(result);
  }

  function showDropHint() {
    setStatus('请在弹出的「拖入文件夹」窗口中拖入文件夹');
    bridge().open_drop_window();
  }

  async function openOutput() {
    const result = await bridge().open_output_folder();
    if (result && result.error) {
      setStatus(result.error);
      log(result.error);
    }
  }

  function toggleMenu(name) {
    const popup = document.querySelector('.menu-popup[data-popup="' + name + '"]');
    const isOpen = popup && !popup.hidden;
    closeMenus();
    if (popup && !isOpen) {
      popup.hidden = false;
      document
        .querySelector('.menu-title[data-menu="' + name + '"]')
        .classList.add('open');
    }
  }

  function closeMenus() {
    document.querySelectorAll('.menu-popup').forEach((popup) => {
      popup.hidden = true;
    });
    document.querySelectorAll('.menu-title').forEach((button) => {
      button.classList.remove('open');
    });
  }

  function runCommand(command) {
    switch (command) {
      case 'choose':
        chooseFolder();
        break;
      case 'refresh':
        if (state.folder) loadFolder(state.folder);
        else setStatus('尚未选择文件夹');
        break;
      case 'open':
        openOutput();
        break;
      case 'quit':
        bridge().quit();
        break;
      case 'guide':
        showDialog(
          '使用说明',
          '1. 把图片文件夹拖入窗口，或点击「打开文件夹…」。\n' +
          '2. 同名 SVG 与图片配对：如 _NG_01246.bmp ↔ _NG_01246.svg。\n' +
          '3. 点击列表行可查看「原图 + SVG」叠加预览。\n' +
          '4. 点击「开始生成」，按原图 3 倍分辨率合成，保存为 {文件名}_叠加图.jpg（JPEG 品质 95）。\n' +
          '5. 勾选「输出到叠加图目录」时输出到所选文件夹下的「叠加图」子文件夹；\n' +
          '   未勾选时输出到原图同目录。已存在时直接覆盖。'
        );
        break;
      case 'about':
        showDialog(
          '关于 SvgOverlayPy',
          'SvgOverlayPy 叠加图生成器\n版本 ' + $('caption-version').textContent +
          '\n\npywebview / WebView2 渲染 · Pillow 解码\n界面风格：Windows 95'
        );
        break;
      default:
        break;
    }
  }

  /* —— 弹窗 / 日志 / 状态栏 ——————————————————————— */

  function showDialog(title, body) {
    $('dialog-title').textContent = title;
    $('dialog-body').textContent = body;
    $('modal').hidden = false;
    $('dialog-close').focus();
  }

  function closeDialog() {
    $('modal').hidden = true;
  }

  function log(text) {
    const box = $('log');
    const line = document.createElement('span');
    line.className = 'line';
    line.dataset.time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    line.textContent = text;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
    while (box.children.length > 300) box.removeChild(box.firstChild);
  }

  function setStatus(text) {
    $('status-left').textContent = text;
  }

  boot();
})();
