/**
 * Dùng chung cho hộp chat của khách (live_chat_widget) và trang Live Chat quản trị:
 * đính kèm ảnh/tệp (nút, kéo thả, Ctrl+V) và chọn emoji.
 *
 *   var composer = ChatComposer({ input, fileInput, attachButton, emojiButton, emojiPanel, tray, dropTarget });
 *   composer.files()  -> các tệp đã chọn      composer.clear()  -> bỏ hết
 *   ChatComposer.attachmentNode({url, name, type}) -> phần tử <a> hiển thị tệp trong khung tin nhắn
 */
(function () {
  var EMOJIS = [
    '😀', '😁', '😂', '🤣', '😊', '😍', '😘', '😎', '🤔',
    '😅', '😢', '😭', '😡', '🙄', '😴', '🤗', '😇', '🥳',
    '👍', '👎', '👌', '🙏', '👏', '💪', '🤝', '👋', '☝️',
    '❤️', '🔥', '🎉', '✅', '❌', '⭐', '⚠️', '💯', '💰',
    '📞', '📍', '📷', '📦', '📄', '🚚', '🏗️', '🏭', '⏰'
  ];

  function isImage(file) {
    return /^image\//.test(file.type);
  }

  function stamp() {
    var d = new Date();
    function two(n) { return (n < 10 ? '0' : '') + n; }
    return d.getFullYear() + two(d.getMonth() + 1) + two(d.getDate()) + '-' + two(d.getHours()) + two(d.getMinutes()) + two(d.getSeconds());
  }

  function create(options) {
    var input = options.input;
    var tray = options.tray;
    var panel = options.emojiPanel;
    var selected = [];
    var previews = [];

    function render() {
      previews.forEach(function (url) { URL.revokeObjectURL(url); });
      previews = [];
      tray.textContent = '';
      tray.hidden = selected.length === 0;
      selected.forEach(function (file, index) {
        var chip = document.createElement('div');
        chip.className = 'chx-chip';
        if (isImage(file)) {
          var img = document.createElement('img');
          img.src = URL.createObjectURL(file);
          previews.push(img.src);
          img.alt = '';
          chip.appendChild(img);
        }
        var name = document.createElement('span');
        name.textContent = file.name;
        chip.appendChild(name);
        var remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = '✕';
        remove.setAttribute('aria-label', 'Bỏ tệp');
        remove.addEventListener('click', function () {
          selected.splice(index, 1);
          render();
        });
        chip.appendChild(remove);
        tray.appendChild(chip);
      });
    }

    function add(list) {
      Array.prototype.forEach.call(list, function (file) {
        // ảnh dán từ clipboard luôn tên "image.png": đặt tên theo giờ để khách và quản trị phân biệt được
        if (/^image\.[a-z]+$/i.test(file.name)) {
          file = new File([file], 'anh-' + stamp() + file.name.slice(file.name.lastIndexOf('.')), { type: file.type });
        }
        selected.push(file);
      });
      render();
    }

    if (options.attachButton && options.fileInput) {
      options.attachButton.addEventListener('click', function () { options.fileInput.click(); });
      options.fileInput.addEventListener('change', function () {
        add(options.fileInput.files);
        options.fileInput.value = '';
      });
    }

    // Ctrl+V: chỉ nhận khi clipboard có tệp (ảnh chụp màn hình) và không có chữ, còn lại để dán chữ như thường
    input.addEventListener('paste', function (event) {
      var data = event.clipboardData;
      if (data && data.files && data.files.length && Array.prototype.indexOf.call(data.types, 'text/plain') === -1) {
        event.preventDefault();
        add(data.files);
      }
    });

    var drop = options.dropTarget;
    if (drop) {
      drop.addEventListener('dragover', function (event) {
        if (event.dataTransfer && Array.prototype.indexOf.call(event.dataTransfer.types, 'Files') !== -1) event.preventDefault();
      });
      drop.addEventListener('drop', function (event) {
        if (event.dataTransfer && event.dataTransfer.files.length) {
          event.preventDefault();
          add(event.dataTransfer.files);
        }
      });
    }

    if (options.emojiButton && panel) {
      function setEmojiOpen(open) {
        panel.hidden = !open;
        options.emojiButton.classList.toggle('is-active', open);
        options.emojiButton.setAttribute('aria-expanded', open ? 'true' : 'false');
      }
      EMOJIS.forEach(function (emoji) {
        var button = document.createElement('button');
        button.type = 'button';
        button.textContent = emoji;
        button.addEventListener('click', function () {
          var start = input.selectionStart == null ? input.value.length : input.selectionStart;
          var end = input.selectionEnd == null ? start : input.selectionEnd;
          input.value = input.value.slice(0, start) + emoji + input.value.slice(end);
          input.focus();
          input.setSelectionRange(start + emoji.length, start + emoji.length);
          input.dispatchEvent(new Event('input', { bubbles: true }));
          setEmojiOpen(false);
        });
        panel.appendChild(button);
      });
      options.emojiButton.addEventListener('click', function (event) {
        event.stopPropagation();
        setEmojiOpen(panel.hidden);
      });
      document.addEventListener('click', function (event) {
        if (!panel.hidden && !panel.contains(event.target)) setEmojiOpen(false);
      });
      document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') setEmojiOpen(false);
      });
    }

    return {
      files: function () { return selected.slice(); },
      clear: function () { selected = []; render(); }
    };
  }

  create.attachmentNode = function (attachment) {
    var link = document.createElement('a');
    link.className = 'chx-attach';
    link.href = attachment.url;
    link.target = '_blank';
    link.rel = 'noopener';
    if (attachment.type === 'image') {
      var img = document.createElement('img');
      img.className = 'chx-attach-img';
      img.src = attachment.url;
      img.alt = attachment.name || '';
      link.appendChild(img);
    } else {
      link.classList.add('chx-attach-file');
      link.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
      var fileLabel = document.createElement('span');
      fileLabel.textContent = attachment.name || 'Tệp';
      link.appendChild(fileLabel);
    }
    return link;
  };

  window.ChatComposer = create;
})();
