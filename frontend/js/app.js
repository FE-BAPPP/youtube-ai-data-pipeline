function getStatusClass(status) {
  if(!status) return '';
  let s = status.toLowerCase();
  if(s === 'pending') return 'status-pending';
  if(s === 'processing' || s === 'running') return 'status-processing';
  if(s === 'completed') return 'status-completed';
  return '';
}

async function loadData() {
  try {
    const response = await fetch('/api/data');
    const data = await response.json();
    
    // Đổ dữ liệu vào bảng Video
    const vTable = document.getElementById('videosTable');
    vTable.innerHTML = '';
    data.videos.forEach(v => {
      let tr = document.createElement('tr');
      let title = v.title.length > 25 ? v.title.substring(0, 25) + '...' : v.title;
      
      let actionHtml = '';
      if (v.status.toUpperCase() === 'COMPLETED') {
        actionHtml = `
          <button class="btn-small" onclick="openIdeaModal('${v.video_id}')" style="margin-right: 5px;">💡 Đẻ Ý Tưởng</button>
          <button class="btn-small" onclick="openFlawModal('${v.video_id}')" style="background: linear-gradient(135deg, #e11d48, #be123c);">🚨 Bắt Lỗi</button>
        `;
      } else {
        actionHtml = `<span style="color:#64748b; font-size:0.85rem;">Đang chờ AI...</span>`;
      }
      
      tr.innerHTML = `
        <td style="font-weight: 600; color: #f8fafc;">${v.channel_name}</td>
        <td style="color: #cbd5e1;" title="${v.title}">${title}</td>
        <td><span class="status-pill ${getStatusClass(v.status)}">${v.status}</span></td>
        <td style="text-align: right;">${actionHtml}</td>
      `;
      vTable.appendChild(tr);
    });

    // Đổ dữ liệu vào bảng Jobs
    const jTable = document.getElementById('jobsTable');
    jTable.innerHTML = '';
    data.jobs.forEach(j => {
      let tr = document.createElement('tr');
      let jobName = j.job_type.replace('_JOB', '');
      tr.innerHTML = `
        <td style="font-weight: 800; color: #a78bfa; letter-spacing: 1px;">${jobName}</td>
        <td><span class="status-pill ${getStatusClass(j.status)}">${j.status}</span></td>
        <td style="font-family: monospace; font-size: 1.2rem; font-weight: bold; color: #f8fafc;">${j.items_processed}</td>
      `;
      jTable.appendChild(tr);
    });

  } catch (e) {
    console.error("Lỗi tải dữ liệu", e);
  }
}

async function searchVideos() {
  const input = document.getElementById('searchInput');
  const keyword = input.value.trim();
  if(!keyword) return;

  const btn = document.querySelector('button');
  btn.innerHTML = 'Đang quét...';
  btn.style.opacity = '0.7';

  try {
    await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword: keyword })
    });
    
    showNotification();
    input.value = '';
    setTimeout(loadData, 1500); // Đợi 1.5s rồi load lại data để thấy video mới
  } catch (e) {
    console.error(e);
  } finally {
    btn.innerHTML = 'Bắt đầu Quét';
    btn.style.opacity = '1';
  }
}

function handleEnter(e) {
  if (e.key === 'Enter') searchVideos();
}

function showNotification() {
  const noti = document.getElementById('notification');
  noti.classList.add('show');
  setTimeout(() => noti.classList.remove('show'), 3500);
}

// Load dữ liệu ngay lập tức khi mở web, sau đó cứ 2 giây tự động làm mới (Real-time ảo)
loadData();
setInterval(loadData, 2000);

// --- MODAL & ĐẺ Ý TƯỞNG LOGIC ---
function closeModal(modalId = 'ideaModal') {
  document.getElementById(modalId).classList.remove('active');
}

async function openIdeaModal(videoId) {
  const modal = document.getElementById('ideaModal');
  const body = document.getElementById('ideaModalBody');
  
  // Hiển thị modal với hiệu ứng loading
  body.innerHTML = `<div class="loader"></div><p style="text-align:center; color:#94a3b8;">Đang vắt sữa ý tưởng từ AI (khoảng 5-10s)...</p>`;
  modal.classList.add('active');

  try {
    const response = await fetch('/api/ideas/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_ids: [videoId], limit: 20 })
    });
    
    const data = await response.json();
    
    if (!response.ok) {
      body.innerHTML = `<div style="text-align:center; color:#f43f5e; padding: 2rem;">❌ ${data.detail || 'Có lỗi xảy ra'}</div>`;
      return;
    }

    // Render danh sách ý tưởng
    let html = '';
    data.ideas.forEach((idea, index) => {
      let pointsHtml = idea.key_points.map(p => `<li>${p}</li>`).join('');
      html += `
        <div class="idea-card">
          <h3 class="idea-title">#${index + 1}: ${idea.title}</h3>
          <p class="idea-rationale">💡 Lỗ hổng: ${idea.rationale}</p>
          <ul class="idea-points">
            ${pointsHtml}
          </ul>
          <span class="idea-emotion">🎭 Cảm xúc mục tiêu: ${idea.target_emotion}</span>
        </div>
      `;
    });
    
    body.innerHTML = html;
  } catch (e) {
    console.error("Lỗi đẻ ý tưởng", e);
    body.innerHTML = `<div style="text-align:center; color:#f43f5e; padding: 2rem;">❌ Không thể kết nối tới AI. Hãy kiểm tra Console.</div>`;
  }
}

async function openFlawModal(videoId) {
  const modal = document.getElementById('flawModal');
  const body = document.getElementById('flawModalBody');
  
  body.innerHTML = `<div class="loader" style="border-left-color: #f43f5e;"></div><p style="text-align:center; color:#94a3b8;">Đang dò quét điểm yếu của đối thủ...</p>`;
  modal.classList.add('active');

  try {
    const response = await fetch('/api/flaws/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_id: videoId, limit: 40 })
    });
    
    const data = await response.json();
    
    if (!response.ok) {
      body.innerHTML = `<div style="text-align:center; color:#f43f5e; padding: 2rem;">❌ ${data.detail || 'Có lỗi xảy ra'}</div>`;
      return;
    }

    const report = data.report;
    let flawsHtml = report.flaws.map(f => `
      <div style="background: rgba(244, 63, 94, 0.05); border-left: 4px solid #f43f5e; padding: 1rem; margin-bottom: 1rem; border-radius: 4px;">
        <h4 style="margin-top: 0; color: #fb7185;">${f.issue}</h4>
        <p style="margin-bottom: 0; color: #cbd5e1; font-style: italic;">"${f.evidence}"</p>
      </div>
    `).join('');

    body.innerHTML = `
      <div class="idea-card" style="border-color: rgba(244, 63, 94, 0.3);">
        <h3 class="idea-title" style="color: #f8fafc;">Bản Án: ${report.summary}</h3>
        <hr style="border: 0; border-top: 1px solid rgba(255,255,255,0.1); margin: 1.5rem 0;" />
        ${flawsHtml}
        <div style="margin-top: 1.5rem; padding: 1.5rem; background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 12px;">
          <h4 style="margin-top: 0; color: #34d399;">⚔️ CHIẾN LƯỢC HỚT TAY TRÊN</h4>
          <p><strong>Gợi ý Tiêu đề:</strong> <span style="color: #f8fafc;">${report.attack_strategy.title_idea}</span></p>
          <p style="margin-bottom: 0;"><strong>Hành động then chốt:</strong> <span style="color: #f8fafc;">${report.attack_strategy.key_action}</span></p>
        </div>
      </div>
    `;
  } catch (e) {
    console.error("Lỗi bắt lỗi", e);
    body.innerHTML = `<div style="text-align:center; color:#f43f5e; padding: 2rem;">❌ Không thể kết nối tới AI. Hãy kiểm tra Console.</div>`;
  }
}
