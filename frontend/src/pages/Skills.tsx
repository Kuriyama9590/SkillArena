import { useEffect, useState } from 'react';
import { api, type SkillInfo } from '../api';
import { FileText, Tag, Eye } from 'lucide-react';

export default function Skills() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string>('');

  useEffect(() => {
    api.skills().then(setSkills).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (selected) {
      api.skill(selected).then((r) => setContent(r.content)).catch(() => setContent(''));
    }
  }, [selected]);

  if (loading) return <div className="p-8 text-center text-gray-500">加载中...</div>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">技能管理</h1>
      <div className="text-sm text-gray-500">已加载 {skills.length} 个技能文件</div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1 space-y-2">
          {skills.map((s) => (
            <button
              key={s.name}
              onClick={() => setSelected(s.name)}
              className={`w-full text-left px-4 py-3 rounded-xl border transition-colors ${
                selected === s.name
                  ? 'bg-blue-50 border-blue-200'
                  : 'bg-white border-gray-200 hover:bg-gray-50'
              }`}
            >
              <div className="flex items-center gap-2">
                <FileText className="w-4 h-4 text-gray-400" />
                <span className="font-mono text-sm text-gray-900">{s.name}</span>
              </div>
              <div className="flex gap-1 mt-2">
                {s.domains.map((d) => (
                  <span
                    key={d}
                    className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-gray-100 rounded text-xs text-gray-600"
                  >
                    <Tag className="w-2.5 h-2.5" />
                    {d}
                  </span>
                ))}
              </div>
              <div className="text-xs text-gray-400 mt-1">{s.content_length} 字符</div>
            </button>
          ))}
        </div>

        <div className="lg:col-span-2">
          {selected ? (
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
              <div className="flex items-center gap-2 mb-3">
                <Eye className="w-4 h-4 text-gray-400" />
                <h2 className="font-mono text-lg font-semibold text-gray-900">{selected}</h2>
              </div>
              <pre className="whitespace-pre-wrap text-sm text-gray-700 bg-gray-50 rounded-lg p-4 max-h-[600px] overflow-auto font-mono">
                {content || '加载中...'}
              </pre>
            </div>
          ) : (
            <div className="flex items-center justify-center h-64 text-gray-400">
              点击左侧技能查看内容
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
