import React, { useEffect, useRef, useState } from 'react';
import {
  AlertCircle,
  CheckCircle,
  Clock,
  Database,
  FileText,
  Loader2,
  Plus,
  Pencil,
  RefreshCw,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import type { AdminAuth, Dataset, Document } from '../api/client';
import {
  createDataset,
  deleteDataset,
  deleteDocument,
  getDatasets,
  getDocuments,
  reindexDocument,
  renameDocument,
  uploadDocument,
} from '../api/client';

function normalizeUploadError(err: any) {
  const message = String(err?.message || err || 'Không thể xử lý tài liệu');
  const normalized = message.toLowerCase();
  if (normalized.includes('429') || normalized.includes('too many requests') || normalized.includes('resource_exhausted') || normalized.includes('quota')) {
    return 'Upload tạm chậm vì hệ thống embedding đang bận. Hệ thống sẽ tự thử lại trong nền, vui lòng đợi thêm một chút.';
  }
  return `Upload thất bại: ${message}`;
}

function normalizeDocumentErrorMessage(message?: string) {
  if (!message) return message;
  const normalized = message.toLowerCase();
  if (normalized.includes('429') || normalized.includes('too many requests') || normalized.includes('resource_exhausted') || normalized.includes('quota')) {
    return 'Hệ thống embedding đang bận và đang tự thử lại trong nền. Bạn chưa cần upload lại ngay.';
  }
  return message;
}

export function DocumentsPanel({ auth }: { auth: AdminAuth }) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [activeDataset, setActiveDataset] = useState<Dataset | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadKey, setUploadKey] = useState(0);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [error, setError] = useState('');
  const [renamingDoc, setRenamingDoc] = useState<Document | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [busyDocId, setBusyDocId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval>>();
  // pollStartRef chỉ được set 1 lần khi bắt đầu polling, KHÔNG reset theo documents state
  const pollStartRef = useRef<number>(0);
  const isPollingRef = useRef<boolean>(false);
  const [stuckDocIds, setStuckDocIds] = useState<Set<string>>(new Set());
  const POLL_MAX_MS = 300_000; // timeout cứng 5 phút
  const POLL_STUCK_MS = 120_000; // hiện cảnh báo "stuck" sau 2 phút

  useEffect(() => {
    loadDatasets();
    return () => clearInterval(pollRef.current);
  }, []);

  useEffect(() => {
    if (activeDataset) loadDocumentsFor(activeDataset.id);
  }, [activeDataset]);

  useEffect(() => {
    const needsPoll = documents.some((d) => d.status === 'pending' || d.status === 'indexing');
    if (needsPoll && activeDataset) {
      // Chỉ bắt đầu interval MỚI nếu chưa đang poll
      if (!isPollingRef.current) {
        isPollingRef.current = true;
        pollStartRef.current = Date.now();
        clearInterval(pollRef.current);
        pollRef.current = setInterval(() => {
          const elapsed = Date.now() - pollStartRef.current;
          if (elapsed > POLL_MAX_MS) {
            clearInterval(pollRef.current);
            isPollingRef.current = false;
            // Đánh dấu tất cả doc còn pending/indexing là stuck
            setStuckDocIds(new Set(
              documents
                .filter((d) => d.status === 'pending' || d.status === 'indexing')
                .map((d) => d.id)
            ));
            return;
          }
          if (elapsed > POLL_STUCK_MS) {
            // Sau 2 phút vẫn chưa xong → hiện cảnh báo stuck
            setStuckDocIds(new Set(
              documents
                .filter((d) => d.status === 'pending' || d.status === 'indexing')
                .map((d) => d.id)
            ));
          }
          loadDocumentsFor(activeDataset.id);
        }, 3000);
      }
    } else {
      // Không còn doc nào đang xử lý → dừng poll và xóa stuck marks
      clearInterval(pollRef.current);
      isPollingRef.current = false;
      if (stuckDocIds.size > 0) setStuckDocIds(new Set());
    }
    return () => {};
  }, [documents, activeDataset]);

  const loadDatasets = async () => {
    try {
      const list = await getDatasets(auth);
      setDatasets(list);
      if (!activeDataset && list[0]) setActiveDataset(list[0]);
    } catch (err: any) {
      setError(err.message || 'Không thể tải danh sách dataset');
    }
  };

  const loadDocumentsFor = async (datasetId: string) => {
    try {
      const docs = await getDocuments(datasetId, auth);
      setDocuments(docs.map((doc) => ({ ...doc, error_message: normalizeDocumentErrorMessage(doc.error_message) })));
    } catch (err: any) {
      setError(err.message || 'Không thể tải tài liệu');
    }
  };

  const handleCreateDataset = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) return;
    try {
      await createDataset(newName.trim(), newDesc.trim(), auth);
      setNewName('');
      setNewDesc('');
      setCreating(false);
      await loadDatasets();
    } catch (err: any) {
      setError(err.message || 'Không thể tạo dataset');
    }
  };

  const handleDeleteDataset = async (id: string) => {
    if (!window.confirm('Xoá dataset và toàn bộ tài liệu?')) return;
    setError('');
    try {
      await deleteDataset(id, auth);
      if (activeDataset?.id === id) {
        setActiveDataset(null);
        setDocuments([]);
      }
      await loadDatasets();
    } catch (err: any) {
      setError(err.message || 'Không thể xoá dataset');
    }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !activeDataset) return;
    setUploading(true);
    setError('');
    try {
      await uploadDocument(activeDataset.id, file, auth);
      await loadDocumentsFor(activeDataset.id);
      await loadDatasets();
    } catch (err: any) {
      setError(normalizeUploadError(err));
    } finally {
      setUploading(false);
      setUploadKey((k) => k + 1);   // force-remount input so same file can be re-selected
    }
  };

  const handleDeleteDoc = async (docId: string) => {
    setError('');
    setBusyDocId(docId);
    try {
      await deleteDocument(docId, auth);
      setDocuments((prev) => prev.filter((d) => d.id !== docId));
      await loadDatasets();
    } catch (err: any) {
      setError(err.message || 'Không thể xoá tài liệu');
    } finally {
      setBusyDocId(null);
    }
  };

  const handleReindex = async (docId: string) => {
    setError('');
    setBusyDocId(docId);
    try {
      await reindexDocument(docId, auth);
      setDocuments((prev) => prev.map((d) => (d.id === docId ? { ...d, status: 'pending', error_message: undefined } : d)));
    } catch (err: any) {
      setError(normalizeUploadError(err));
    } finally {
      setBusyDocId(null);
    }
  };

  const handleRenameDoc = async () => {
    if (!renamingDoc || !renameValue.trim()) return;
    setError('');
    setBusyDocId(renamingDoc.id);
    try {
      await renameDocument(renamingDoc.id, renameValue.trim(), auth);
      setDocuments((prev) => prev.map((d) => (d.id === renamingDoc.id ? { ...d, name: renameValue.trim() } : d)));
      setRenamingDoc(null);
      setRenameValue('');
    } catch (err: any) {
      setError(err.message || 'Không thể đổi tên tài liệu');
    } finally {
      setBusyDocId(null);
    }
  };

  return (
    <div className="flex h-full bg-[#fff9f6] overflow-hidden">
      <aside className="w-72 bg-white border-r border-[#ead8cf] flex flex-col">
        <div className="p-4 border-b border-[#ead8cf] flex items-center justify-between">
          <div>
            <h2 className="font-semibold text-[#6b4637] text-sm">Tài liệu nội bộ</h2>
            <p className="text-xs text-[#9a7868]">Chỉ admin mới có quyền quản lý</p>
          </div>
          <button
            onClick={() => setCreating(true)}
            className="p-2 text-[#a86a4f] hover:bg-[#fff7f2] rounded-xl transition-colors"
            title="Thêm dataset"
          >
            <Plus size={16} />
          </button>
        </div>

        {creating && (
          <form onSubmit={handleCreateDataset} className="p-3 border-b border-slate-100 bg-[#fff7f2]">
            <input
              autoFocus
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Tên dataset *"
              className="w-full text-sm border border-[#dcc3b7] rounded-xl px-3 py-2 mb-2 focus:outline-none focus:border-[#b97b61]"
            />
            <input
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="Mô tả (tuỳ chọn)"
              className="w-full text-sm border border-[#dcc3b7] rounded-xl px-3 py-2 mb-2 focus:outline-none focus:border-[#b97b61]"
            />
            <div className="flex gap-2">
              <button type="submit" className="flex-1 text-sm bg-[#a86a4f] text-white rounded-xl py-2 hover:bg-[#945843]">Tạo</button>
              <button type="button" onClick={() => setCreating(false)} className="px-3 rounded-xl text-[#8c6a5b] hover:bg-white">
                <X size={16} />
              </button>
            </div>
          </form>
        )}

        <div className="flex-1 overflow-y-auto py-2 space-y-1 px-2">
          {datasets.length === 0 && (
            <p className="text-[#9a7868] text-xs text-center py-8">Chưa có dataset</p>
          )}
          {datasets.map((ds) => (
            <div
              key={ds.id}
              onClick={() => setActiveDataset(ds)}
              className={`group flex items-start gap-3 px-3 py-3 rounded-2xl cursor-pointer transition-colors ${
                activeDataset?.id === ds.id ? 'bg-[#fff4ee] border border-[#d9b7a7]' : 'hover:bg-[#fff9f6] border border-transparent'
              }`}
            >
              <Database size={15} className="shrink-0 mt-0.5 text-[#9a7868]" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-[#734232] truncate">{ds.name}</p>
                <p className="text-xs text-[#9a7868]">{ds.ready_count}/{ds.document_count} tài liệu sẵn sàng</p>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); handleDeleteDataset(ds.id); }}
                className="opacity-0 group-hover:opacity-100 p-1 text-[#9a7868] hover:text-red-500 transition-all"
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      </aside>

      <main className="flex-1 flex flex-col overflow-hidden">
        {activeDataset ? (
          <>
            <div className="bg-white border-b border-[#ead8cf] px-6 py-4 flex items-center justify-between">
              <div>
                <h2 className="font-semibold text-[#6b4637]">{activeDataset.name}</h2>
                {activeDataset.description && <p className="text-sm text-[#9a7868] mt-1">{activeDataset.description}</p>}
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => activeDataset && loadDocumentsFor(activeDataset.id)}
                  className="flex items-center gap-2 px-3 py-2 border border-[#e0c5b8] text-[#8c533f] rounded-2xl hover:bg-[#fff7f2] text-sm"
                >
                  <RefreshCw size={14} /> Làm mới
                </button>
                <input
                  key={uploadKey}
                  ref={fileRef}
                  type="file"
                  className="hidden"
                  accept=".pdf,.txt,.md,.docx,.csv,.html"
                  onChange={handleUpload}
                />
                <button
                  onClick={() => fileRef.current?.click()}
                  disabled={uploading}
                  className="flex items-center gap-2 px-4 py-2 bg-[#a86a4f] hover:bg-[#945843] disabled:bg-[#d7b7a9] text-white text-sm rounded-2xl transition-colors"
                >
                  {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                  {uploading ? 'Đang upload… (có thể mất vài phút)' : 'Upload tài liệu'}
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-6">
              {error && (
                <div className="mb-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600 flex items-center gap-2">
                  <AlertCircle size={16} /> {error}
                </div>
              )}

              {documents.length === 0 ? (
                <div className="text-center mt-16 text-[#9a7868]">
                  <FileText size={40} className="mx-auto mb-3 opacity-40" />
                  <p className="text-sm">Chưa có tài liệu nào</p>
                  <p className="text-xs mt-1">Upload PDF, TXT, DOCX, MD, CSV, HTML</p>
                </div>
              ) : (
                <div className="grid gap-3 max-w-4xl">
                  {documents.map((doc) => (
                    <DocumentCard key={doc.id} doc={doc} busy={busyDocId === doc.id} stuck={stuckDocIds.has(doc.id)} onDelete={handleDeleteDoc} onReindex={handleReindex} onRename={(doc) => { setRenamingDoc(doc); setRenameValue(doc.name); }} />
                  ))}
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-[#9a7868]">
            <div className="text-center">
              <Database size={48} className="mx-auto mb-3 opacity-30" />
              <p className="text-sm">Chọn dataset để xem tài liệu</p>
            </div>
          </div>
        )}
      </main>

      {renamingDoc && (
        <div className="fixed inset-0 z-50 bg-slate-900/25 backdrop-blur-[1px] flex items-center justify-center p-4">
          <div className="w-full max-w-md rounded-3xl border border-[#ead8cf] bg-white shadow-[0_20px_60px_rgba(15,23,42,0.18)] p-6">
            <div className="flex items-center justify-between gap-3 mb-4">
              <div>
                <h3 className="text-base font-semibold text-[#6b4637]">Đổi tên tài liệu</h3>
                <p className="text-sm text-[#9a7868]">Cập nhật tên hiển thị của tài liệu</p>
              </div>
              <button onClick={() => { setRenamingDoc(null); setRenameValue(''); }} className="p-2 rounded-xl text-[#9a7868] hover:bg-[#fff7f2] hover:text-slate-600">
                <X size={16} />
              </button>
            </div>
            <input
              autoFocus
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              placeholder="Nhập tên tài liệu"
              className="w-full rounded-2xl border border-[#dcc3b7] px-4 py-3 text-sm focus:outline-none focus:border-[#b97b61]"
            />
            <div className="mt-5 flex items-center justify-end gap-2">
              <button onClick={() => { setRenamingDoc(null); setRenameValue(''); }} className="px-4 py-2 rounded-2xl border border-[#ead8cf] text-sm text-[#8c6a5b] hover:bg-[#fff9f6]">Hủy</button>
              <button onClick={handleRenameDoc} className="px-4 py-2 rounded-2xl bg-indigo-600 hover:bg-indigo-700 text-sm text-white">Lưu tên mới</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function DocumentCard({
  doc,
  onDelete,
  onReindex,
  onRename,
  busy = false,
  stuck = false,
}: {
  doc: Document;
  busy?: boolean;
  stuck?: boolean;
  onDelete: (id: string) => void;
  onReindex: (id: string) => void;
  onRename: (doc: Document) => void;
}) {
  const isStuck = (doc.status === 'indexing' || doc.status === 'pending') && stuck;

  const statusIcon = isStuck
    ? <AlertCircle size={14} className="text-orange-400" />
    : {
        ready: <CheckCircle size={14} className="text-green-500" />,
        indexing: <Loader2 size={14} className="text-indigo-500 animate-spin" />,
        pending: <Clock size={14} className="text-amber-500" />,
        error: <AlertCircle size={14} className="text-red-500" />,
      }[doc.status];

  const statusText = isStuck
    ? 'Xử lý quá lâu'
    : {
        ready: 'Sẵn sàng',
        indexing: 'Đang xử lý...',
        pending: 'Chờ xử lý',
        error: 'Lỗi',
      }[doc.status];

  const sizeStr = doc.file_size > 1024 * 1024
    ? `${(doc.file_size / (1024 * 1024)).toFixed(1)} MB`
    : `${(doc.file_size / 1024).toFixed(0)} KB`;

  return (
    <div className="bg-white border border-[#ead8cf] rounded-2xl px-5 py-4 flex items-start gap-4 hover:border-[#dcc3b7] transition-colors">
      <div className="w-10 h-10 bg-slate-100 rounded-xl flex items-center justify-center shrink-0">
        <FileText size={16} className="text-[#8c6a5b]" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-[#6b4637] truncate">{doc.name}</p>
        <div className="mt-1 flex flex-wrap gap-2 text-xs text-[#9a7868]">
          <span>{doc.file_type?.toUpperCase()}</span>
          <span>•</span>
          <span>{sizeStr}</span>
          <span>•</span>
          <span>{doc.chunk_count} chunk</span>
        </div>
        {doc.error_message && <p className="text-xs text-red-500 mt-2">{normalizeDocumentErrorMessage(doc.error_message)}</p>}
        {isStuck && !doc.error_message && (
          <p className="text-xs text-orange-500 mt-2">
            Xử lý quá lâu — hãy thử{' '}
            <button onClick={() => onReindex(doc.id)} className="underline font-medium hover:text-orange-700">
              reindex lại
            </button>
            .
          </p>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <div className="flex items-center gap-1.5 rounded-full px-2.5 py-1 bg-[#fff9f6] border border-[#ead8cf] text-xs text-[#8c6a5b]">
          {statusIcon}
          {statusText}
        </div>
        <button
          disabled={busy}
          onClick={(e) => { e.stopPropagation(); onRename(doc); }}
          className="p-2 rounded-xl text-[#9a7868] hover:text-[#734232] hover:bg-[#fff7f2] disabled:opacity-50"
          title="Đổi tên tài liệu"
        >
          <Pencil size={14} />
        </button>
        <button
          disabled={busy}
          onClick={(e) => { e.stopPropagation(); onReindex(doc.id); }}
          className="p-2 rounded-xl text-[#9a7868] hover:text-[#a86a4f] hover:bg-[#fff7f2] disabled:opacity-50"
          title="Re-index tài liệu"
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
        </button>
        <button
          disabled={busy}
          onClick={(e) => { e.stopPropagation(); onDelete(doc.id); }}
          className="p-2 rounded-xl text-[#9a7868] hover:text-red-500 hover:bg-red-50 disabled:opacity-50"
          title="Xoá tài liệu"
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
}
