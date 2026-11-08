(() => {
  const textarea = document.querySelector('#body');
  const count = document.querySelector('#count');
  if (textarea && count) {
    const maximum = Number(textarea.maxLength);
    const update = () => { count.textContent = `${textarea.value.length.toLocaleString()} / ${maximum.toLocaleString()}`; };
    textarea.addEventListener('input', update);
    update();
  }

  document.querySelectorAll('[data-copy-target]').forEach((button) => {
    button.addEventListener('click', async () => {
      const target = document.getElementById(button.dataset.copyTarget);
      if (!target) return;
      const value = 'value' in target ? target.value : target.textContent;
      try {
        await navigator.clipboard.writeText(value);
      } catch (_error) {
        target.focus();
        if ('select' in target) target.select();
        document.execCommand('copy');
      }
      const original = button.textContent;
      button.textContent = 'Copied';
      window.setTimeout(() => { button.textContent = original; }, 1400);
    });
  });
})();
