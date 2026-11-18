document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('.toast').forEach(t=>setTimeout(()=>t.remove(),4500));
  document.querySelectorAll('form.filterbar').forEach(f=>f.addEventListener('submit',()=>{}));

  const themeToggle=document.querySelector('[data-theme-toggle]');
  if(themeToggle){
    const themeMeta=document.querySelector('meta[name="theme-color"]');
    const syncThemeButton=()=>{
      const isDark=document.documentElement.dataset.theme==='dark';
      const label=isDark?'Light mode':'Dark mode';
      themeToggle.setAttribute('aria-pressed',String(isDark));
      themeToggle.setAttribute('aria-label',`Switch to ${isDark?'light':'dark'} theme`);
      themeToggle.title=`Switch to ${isDark?'light':'dark'} theme`;
      themeToggle.querySelector('.theme-toggle-icon').textContent=isDark?'☀':'☾';
      themeToggle.querySelector('.theme-toggle-label').textContent=label;
      if(themeMeta)themeMeta.content=isDark?'#111813':'#006e0f';
    };
    syncThemeButton();
    themeToggle.addEventListener('click',()=>{
      const nextTheme=document.documentElement.dataset.theme==='dark'?'light':'dark';
      if(nextTheme==='dark')document.documentElement.dataset.theme='dark';
      else delete document.documentElement.dataset.theme;
      try{localStorage.setItem('estatehub-theme',nextTheme)}catch(error){}
      syncThemeButton();
    });
  }
});
