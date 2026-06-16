import { useEffect, useState } from 'react';
import { api, type ReportInfo } from '../api';
import { FileText } from 'lucide-react';

export default function Reports() {
  const [reports, setReports] = useState<ReportInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState('');

  useEffect(() => {
    api.reports().then(setReports).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (selected) {
      api.report(selected).then((r) => setContent(r.content)).catch(() => setContent(''));
    }
  }, [selected]);

  if (loading) return <div className="p-8 text-center text-gray-500">加载中...</div>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">报告</h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1 space-y-2">
          {reports.length === 0 ? (
            <div className="text-gray-500 text-sm">暂无生成的报告。</div>
          ) : (
            reports.map((r) => (
              <button
                key={r.filename}
                onClick={() => setSelected(r.filename)}
                className={`w-full text-left px-4 py-3 rounded-xl border transition-colors ${
                  selected === r.filename
                    ? 'bg-blue-50 border-blue-200'
                    : 'bg-white border-gray-200 hover:bg-gray-50'
                }`}
              >
                <div className="flex items-center gap-2">
                  <FileText className="w-4 h-4 text-gray-400" />
                  <span className="text-sm text-gray-900">{r.filename}</span>
                </div>
                <div className="text-xs text-gray-400 mt-1">
                  {(r.size / 1024).toFixed(1)} KB · {new Date(r.modified * 1000).toLocaleString()}
                </div>
              </button>
            ))
          )}
        </div>

        <div className="lg:col-span-2">
          {selected ? (
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
              <h2 className="text-lg font-semibold text-gray-900 mb-3">{selected}</h2>
              <pre className="whitespace-pre-wrap text-sm text-gray-700 bg-gray-50 rounded-lg p-4 max-h-[600px] overflow-auto font-mono">
                {content || '加载中...'}
              </pre>
            </div>
          ) : (
            <div className="flex items-center justify-center h-64 text-gray-400">
              选择左侧报告查看内容
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
