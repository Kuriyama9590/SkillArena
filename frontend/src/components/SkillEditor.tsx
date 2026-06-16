import { useState, useEffect } from 'react';
import { X, Save } from 'lucide-react';

interface Props {
  mode: 'create' | 'edit';
  initialName?: string;
  initialContent?: string;
  onSave: (name: string, content: string) => Promise<void>;
  onClose: () => void;
}

export default function SkillEditor({ mode, initialName = '', initialContent = '', onSave, onClose }: Props) {
  const [name, setName] = useState(initialName);
  const [content, setContent] = useState(initialContent);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setName(initialName);
    setContent(initialContent);
  }, [initialName, initialContent]);

  const handleSave = async () => {
    if (!name.trim()) { setError('名称不能为空'); return; }
    if (!content.trim()) { setError('内容不能为空'); return; }
    setSaving(true);
    setError('');
    try {
      await onSave(name.trim(), content.trim());
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-[720px] max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h2 className="text-lg font-bold text-gray-900">
            {mode === 'create' ? '新建技能' : `编辑 · ${initialName}`}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto p-5 space-y-4">
          {mode === 'create' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">技能名称</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. concise-writer"
                className="w-full px-3 py-2 rounded-lg border border-gray-300 text-sm focus:border-blue-400 focus:ring-1 focus:ring-blue-400 outline-none"
              />
              <p className="text-xs text-gray-400 mt-1">仅支持字母、数字、连字符和下划线</p>
            </div>
          )}
          <div className="flex-1">
            <label className="block text-sm font-medium text-gray-700 mb-1">内容 (Markdown)</label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={`---\ndomains: [writing]\n---\n\n# Skill Name\n\n## 核心原则\n\n1. ...`}
              className="w-full h-80 px-3 py-2 rounded-lg border border-gray-300 text-sm font-mono focus:border-blue-400 focus:ring-1 focus:ring-blue-400 outline-none resize-none"
            />
          </div>
          {error && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 px-3 py-2 rounded-lg">
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-gray-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900 border border-gray-200 rounded-lg"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 text-sm text-white bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-50"
          >
            <Save className="w-4 h-4" />
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}
