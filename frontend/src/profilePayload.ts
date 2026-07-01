interface ProfilePayloadInput {
  profileName: string
  profileDesc: string
  testsPath: string
  selectedRunner: string
  selectedFiles: string[]
  selectedMarkers: string[]
  customArgs: string
  timeoutSeconds: number | ''
  env: Record<string, string>
}

export function buildProfilePayload(input: ProfilePayloadInput) {
  return {
    name: input.profileName.trim(),
    description: input.profileDesc.trim() || null,
    tests_path: input.testsPath,
    runner: input.selectedRunner,
    selected_files: input.selectedFiles,
    selected_markers: input.selectedMarkers,
    extra_args: input.customArgs,
    timeout: input.timeoutSeconds === '' ? null : Number(input.timeoutSeconds),
    env: input.env,
  }
}
