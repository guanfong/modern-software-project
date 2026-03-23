import { useState, useEffect } from 'react';
import { formatExtractedFields } from '../lib/formatExtractedFields';
import axios from '../lib/axios';
import { getApiErrorMessage } from '../lib/apiErrorMessage';
import { FiUpload, FiMic, FiStopCircle, FiX } from 'react-icons/fi';

export default function HRBriefingsPage() {
  const [briefings, setBriefings] = useState<any[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);
  const [roles, setRoles] = useState<any[]>([]);

  useEffect(() => {
    fetchBriefings();
    fetchRoles();
  }, []);

  const fetchBriefings = async () => {
    try {
      const response = await axios.get('/api/hr-briefings');
      setBriefings(response.data.briefings || []);
    } catch (error) {
      console.error('Error fetching briefings:', error);
    }
  };

  const fetchRoles = async () => {
    try {
      const response = await axios.get('/api/roles');
      setRoles(response.data.roles || []);
    } catch (error) {
      console.error('Error fetching roles:', error);
    }
  };

  const updateBriefingRoles = async (briefingId: string, roleIds: string[]) => {
    try {
      await axios.put(`/api/hr-briefings/${briefingId}/roles`, { role_ids: roleIds });
      fetchBriefings();
    } catch (error) {
      console.error('Error updating briefing roles:', error);
      alert('Failed to update assigned roles');
    }
  };

  const handleRemoveRole = (briefing: any, roleId: string) => {
    const currentIds = briefing.role_ids ?? [];
    updateBriefingRoles(briefing.id, currentIds.filter((id: string) => id !== roleId));
  };

  const handleAddRole = (briefing: any, roleId: string) => {
    const currentIds = briefing.role_ids ?? [];
    if (currentIds.includes(roleId)) return;
    updateBriefingRoles(briefing.id, [...currentIds, roleId]);
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    formData.append('role_ids', selectedRoles.join(','));

    try {
      // Do not set Content-Type manually — axios must add the multipart boundary.
      // Transcription + CrewAI often exceeds the default 30s axios timeout.
      await axios.post('/api/hr-briefings', formData, { timeout: 300000 });
      fetchBriefings();
      setSelectedRoles([]);
    } catch (error: unknown) {
      console.error('Error uploading briefing:', error);
      alert(`Failed to upload HR briefing: ${getApiErrorMessage(error)}`);
    } finally {
      setIsUploading(false);
    }
  };

  const handleStartRecording = () => {
    // TODO: Implement real-time recording
    setIsRecording(true);
    alert('Real-time recording not yet implemented. Please upload an audio file.');
    setIsRecording(false);
  };

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <h1 className="text-4xl font-bold text-gray-900 mb-2">HR Briefings</h1>
          <p className="text-gray-600">Upload or record HR briefings to extract key details</p>
        </div>

        <div className="bg-white rounded-lg shadow p-6 mb-8">
          <h2 className="text-xl font-semibold mb-4">New Briefing</h2>

          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Assign to Roles (optional)
            </label>
            <div className="space-y-2 max-h-40 overflow-y-auto border border-gray-300 rounded-lg p-3">
              {roles.map((role) => (
                <label key={role.id} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={selectedRoles.includes(role.id)}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setSelectedRoles([...selectedRoles, role.id]);
                      } else {
                        setSelectedRoles(selectedRoles.filter((id) => id !== role.id));
                      }
                    }}
                    className="rounded"
                  />
                  <span className="text-sm">{role.title}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="flex gap-4">
            <label className="flex items-center gap-2 bg-blue-600 text-white px-6 py-3 rounded-lg hover:bg-blue-700 cursor-pointer">
              <FiUpload className="w-5 h-5" />
              {isUploading ? 'Uploading...' : 'Upload Audio File'}
              <input
                type="file"
                accept="audio/*"
                onChange={handleFileUpload}
                className="hidden"
                disabled={isUploading}
              />
            </label>

            <button
              onClick={handleStartRecording}
              disabled={isRecording}
              className={`flex items-center gap-2 px-6 py-3 rounded-lg ${
                isRecording
                  ? 'bg-red-600 text-white hover:bg-red-700'
                  : 'bg-gray-600 text-white hover:bg-gray-700'
              }`}
            >
              {isRecording ? (
                <>
                  <FiStopCircle className="w-5 h-5" />
                  Stop Recording
                </>
              ) : (
                <>
                  <FiMic className="w-5 h-5" />
                  Start Recording
                </>
              )}
            </button>
          </div>
        </div>

        <div className="space-y-4">
          <h2 className="text-2xl font-semibold">Recent Briefings</h2>
          {briefings.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <p>No briefings yet</p>
              <p className="text-sm mt-2">Upload or record a briefing to get started</p>
            </div>
          ) : (
            briefings.map((briefing) => (
              <div key={briefing.id} className="bg-white rounded-lg shadow p-6">
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-lg font-semibold">Briefing #{briefing.id.slice(0, 8)}</h3>
                    <p className="text-sm text-gray-500">
                      {new Date(briefing.created_at).toLocaleString()}
                    </p>
                  </div>
                </div>

                <div className="space-y-4">
                  <div>
                    <h4 className="font-semibold text-gray-900 mb-2">Summary</h4>
                    <p className="text-gray-700">{briefing.summary || 'N/A'}</p>
                  </div>

                  <div>
                    <h4 className="font-semibold text-gray-900 mb-2">Extracted Fields</h4>
                    <div className="bg-gray-50 p-4 rounded text-sm whitespace-pre-wrap text-gray-700">
                      {formatExtractedFields(briefing.extracted_fields)}
                    </div>
                  </div>

                  {(() => {
                    const list = briefing.assigned_roles?.length
                      ? briefing.assigned_roles
                      : (briefing.role_ids ?? []).map((roleId: string) => ({
                          id: roleId,
                          title: roles.find((r) => r.id === roleId)?.title ?? roleId,
                        }));
                    const assignedIds = briefing.role_ids ?? [];
                    const rolesToAdd = roles.filter((r) => r.id && !assignedIds.includes(r.id));
                    return (
                      <div>
                        <h4 className="font-semibold text-gray-900 mb-2">Assigned Roles</h4>
                        <div className="flex flex-wrap items-center gap-2">
                          {list.map((item: { id: string; title: string }) => (
                            <span
                              key={item.id}
                              className="inline-flex items-center gap-1 bg-blue-100 text-blue-800 pl-3 pr-1 py-1 rounded-full text-sm"
                            >
                              {item.title || item.id}
                              <button
                                type="button"
                                onClick={() => handleRemoveRole(briefing, item.id)}
                                className="p-0.5 rounded hover:bg-blue-200 text-blue-800"
                                title="Remove role"
                                aria-label={`Remove ${item.title || item.id}`}
                              >
                                <FiX className="w-3.5 h-3.5" />
                              </button>
                            </span>
                          ))}
                          {rolesToAdd.length > 0 && (
                            <select
                              value=""
                              onChange={(e) => {
                                const id = e.target.value;
                                if (id) {
                                  handleAddRole(briefing, id);
                                  e.target.value = '';
                                }
                              }}
                              className="text-sm border border-gray-300 rounded-full py-1 pl-3 pr-8 bg-gray-50 text-gray-700"
                              title="Add role"
                            >
                              <option value="">+ Add role</option>
                              {rolesToAdd.map((r) => (
                                <option key={r.id} value={r.id}>
                                  {r.title}
                                </option>
                              ))}
                            </select>
                          )}
                        </div>
                      </div>
                    );
                  })()}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

