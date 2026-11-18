document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('.toast').forEach(t=>setTimeout(()=>t.remove(),4500));
  document.querySelectorAll('form.filterbar').forEach(f=>f.addEventListener('submit',()=>{}));
});
