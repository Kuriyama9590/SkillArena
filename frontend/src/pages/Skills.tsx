import { useEffect, useState, useMemo } from 'react';
import { api, type SkillInfo } from '../api';
import { FileText, Tag, Plus, Pencil, Trash2, ChevronDown, ChevronRight, Search } from 'lucide-react';
import SkillEditor from '../components/SkillEditor';

const ALL_DOMAINS = ['writing', 'coding', 'analysis', 'general'] as const;

const DOMAIN_COLORS: Record<string, { bg: string; text: string; active: string }> = {
  writing: { bg: 'bg-blue-100', text: 'text-blue-700', active: 'bg-blue-600 text-white' },
  coding: { bg: 'bg-green-100', text: 'text-green-700', active: 'bg-green-600 text-white' },
  analysis: { bg: 'bg-amber-100', text: 'text-amber-700', active: 'bg-amber-600 text-white' },
  general: { bg: 'bg-purple-100', text: 'text-purple-700', active: 'bg-purple-600 text-white' },
};

export default function Skills() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [contentCache, setContentCache] = useState<Map<string, string>>(new Map());
  const [activeDomains, setActiveDomains] = useState<Set<string>>(new Set());
  const [collapsedDomains, setCollapsedDomains] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const [editor, setEditor] = useState<{ mode: 'create' | 'edit'; name?: string; content?: string } | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const reload = () => {
    api.skills().then(setSkills).finally(() => setLoading(false));
  };

  useEffect(() => { reload(); }, []);

  // Filter skills by active domains and search
  const filtered = useMemo(() => {
    return skills.filter((s) => {
      if (activeDomains.size > 0 && !s.domains.some((d) => activeDomains.has(d))) return false;
      if (search && !s.name.toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    });
  }, [skills, activeDomains, search]);

  // Group by primary domain
  const grouped = useMemo(() => {
    const groups: Record<string, SkillInfo[]> = {};
    for (const s of filtered) {
      const key = s.domains[0] || 'general';
      (groups[key] ??= []).push(s);
    }
    return groups;
  }, [filtered]);

  const toggleDomain = (d: string) => {
    setActiveDomains((prev) => {
      const next = new Set(prev);
      next.has(d) ? next.delete(d) : next.add(d);
      return next;
    });
  };

  const toggleDomainCollapse = (d: string) => {
    setCollapsedDomains((prev) => {
      const next = new Set(prev);
      next.has(d) ? next.delete(d) : next.add(d);
      return next;
    });
  };

  const toggleExpand = (name: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
        // lazy-load content
        if (!contentCache.has(name)) {
          api.skill(name).then((r) => {
            setContentCache((c) => new Map(c).set(name, r.content));
          });
        }
      }
      return next;
    });
  };

  const handleSave = async (name: string, content: string) => {
    if (editor?.mode === 'edit' && editor.name) {
      await api.updateSkill(editor.name, content);
      setContentCache((c) => new Map(c).set(editor.name!, content));
    } else {
      await api.uploadSkill(name, content);
    }
    reload();
  };

  const handleDelete = async (name: string) => {
    await api.deleteSkill(name);
    setExpanded((prev) => { const n = new Set(prev); n.delete(name); return n; });
    setContentCache((prev) => { const n = new Map(prev); n.delete(name); return n; });
    setDeleteConfirm(null);
    reload();
  };

  if (loading) return <div className="p-8 text-center text-gray-500">加载中...</div>;

  const domainOrder = ['writing', 'coding', 'analysis', 'general'];
  const sortedGroups = domainOrder.filter((d) => grouped[d]?.length);

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">技能管理</h1>
          <div className="text-sm text-gray-500 mt-0.5">
            {skills.length} 个技能
            {activeDomains.size > 0 && ` · 筛选显示 ${filtered.length} 个`}
          </div>
        </div>
        <button
          onClick={() => setEditor({ mode: 'create' })}
          className="flex items-center gap-2 px-4 py-2 text-sm text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
        >
          <Plus className="w-4 h-4" /> 新建技能
        </button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Domain chips */}
        <div className="flex items-center gap-1.5">
          <Tag className="w-3.5 h-3.5 text-gray-400" />
          {ALL_DOMAINS.map((d) => {
            const c = DOMAIN_COLORS[d];
            const active = activeDomains.has(d);
            const count = skills.filter((s) => s.domains.includes(d)).length;
            return (
              <button
                key={d}
                onClick={() => toggleDomain(d)}
                className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                  active ? c.active : `${c.bg} ${c.text} hover:opacity-80`
                }`}
              >
                {d} <span className="opacity-60">{count}</span>
              </button>
            );
          })}
          {activeDomains.size > 0 && (
            <button
              onClick={() => setActiveDomains(new Set())}
              className="text-xs text-gray-400 hover:text-gray-600 ml-1"
            >
              清除
            </button>
          )}
        </div>

        {/* Search */}
        <div className="relative ml-auto">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索技能..."
            className="pl-8 pr-3 py-1.5 w-52 text-sm border border-gray-200 rounded-lg focus:border-blue-400 outline-none"
          />
        </div>
      </div>

      {/* Skill list — grouped by domain */}
      {sortedGroups.length === 0 ? (
        <div className="text-center text-gray-400 py-12">
          {skills.length === 0 ? '暂无技能，点击右上角创建' : '无匹配技能'}
        </div>
      ) : (
        <div className="space-y-4">
          {sortedGroups.map((domain) => {
            const c = DOMAIN_COLORS[domain];
            return (
              <div key={domain}>
                {/* Group header — clickable to collapse */}
                <button
                  onClick={() => toggleDomainCollapse(domain)}
                  className="flex items-center gap-2 mb-2 group"
                >
                  {collapsedDomains.has(domain) ? (
                    <ChevronRight className="w-3.5 h-3.5 text-gray-400" />
                  ) : (
                    <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
                  )}
                  <span className={`px-2 py-0.5 rounded text-xs font-bold ${c.active}`}>
                    {domain}
                  </span>
                  <span className="text-xs text-gray-400">{grouped[domain].length} 个</span>
                </button>

                {/* Cards — hidden when collapsed */}
                {!collapsedDomains.has(domain) && (
                <div className="space-y-1.5">
                  {grouped[domain].map((s) => {
                    const isOpen = expanded.has(s.name);
                    const content = contentCache.get(s.name);
                    return (
                      <div
                        key={s.name}
                        className={`bg-white rounded-lg border transition-colors ${
                          isOpen ? 'border-blue-200 shadow-sm' : 'border-gray-200 hover:border-gray-300'
                        }`}
                      >
                        {/* Collapsed header row */}
                        <button
                          onClick={() => toggleExpand(s.name)}
                          className="w-full flex items-center gap-3 px-4 py-2.5 text-left"
                        >
                          {isOpen ? (
                            <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
                          ) : (
                            <ChevronRight className="w-4 h-4 text-gray-400 flex-shrink-0" />
                          )}
                          <FileText className="w-4 h-4 text-gray-400 flex-shrink-0" />
                          <span className="font-mono text-sm text-gray-900 flex-1 truncate">
                            {s.name}
                          </span>
                          <span className="text-xs text-gray-400 flex-shrink-0">
                            {s.content_length} 字符
                          </span>
                          {/* Domain tags */}
                          <div className="flex gap-1 flex-shrink-0">
                            {s.domains.map((d) => {
                              const dc = DOMAIN_COLORS[d] || DOMAIN_COLORS.general;
                              return (
                                <span
                                  key={d}
                                  className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${dc.bg} ${dc.text}`}
                                >
                                  {d}
                                </span>
                              );
                            })}
                          </div>
                        </button>

                        {/* Expanded content */}
                        {isOpen && (
                          <div className="px-4 pb-3 border-t border-gray-100">
                            <div className="flex items-center justify-end gap-1.5 py-2">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  const c = contentCache.get(s.name);
                                  if (c !== undefined) {
                                    setEditor({ mode: 'edit', name: s.name, content: c });
                                  } else {
                                    api.skill(s.name).then((r) =>
                                      setEditor({ mode: 'edit', name: s.name, content: r.content }),
                                    );
                                  }
                                }}
                                className="flex items-center gap-1 px-2.5 py-1 text-xs text-gray-500 hover:text-blue-600 border border-gray-200 hover:border-blue-300 rounded"
                              >
                                <Pencil className="w-3 h-3" /> 编辑
                              </button>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setDeleteConfirm(s.name);
                                }}
                                className="flex items-center gap-1 px-2.5 py-1 text-xs text-gray-500 hover:text-red-600 border border-gray-200 hover:border-red-300 rounded"
                              >
                                <Trash2 className="w-3 h-3" /> 删除
                              </button>
                            </div>
                            <pre className="whitespace-pre-wrap text-xs text-gray-700 bg-gray-50 rounded-lg p-3 max-h-[400px] overflow-auto font-mono leading-relaxed">
                              {content ?? '加载中...'}
                            </pre>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Editor Modal */}
      {editor && (
        <SkillEditor
          mode={editor.mode}
          initialName={editor.name}
          initialContent={editor.content}
          onSave={handleSave}
          onClose={() => setEditor(null)}
        />
      )}

      {/* Delete Confirmation */}
      {deleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setDeleteConfirm(null)}>
          <div className="bg-white rounded-2xl shadow-2xl p-6 w-[400px]" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-gray-900 mb-2">确认删除</h3>
            <p className="text-sm text-gray-600 mb-4">
              确定要删除技能 <span className="font-mono font-semibold">{deleteConfirm}</span> 吗？此操作不可撤销。
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setDeleteConfirm(null)}
                className="px-4 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50"
              >
                取消
              </button>
              <button
                onClick={() => handleDelete(deleteConfirm)}
                className="px-4 py-2 text-sm text-white bg-red-600 hover:bg-red-700 rounded-lg"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
