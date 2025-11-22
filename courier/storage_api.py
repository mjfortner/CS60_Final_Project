class StorageAPI(object):
    """
    Contract for how code talks to storage.
    Implementation can use SQLite, files, etc.,
    but must keep the same method names and parameters.
    """

    def save_bundle(self, bundle_dict):
        raise NotImplementedError()

    def load_bundle(self, bundle_id):
        raise NotImplementedError()

    def update_bundle_state(self, bundle_id, new_state):
        raise NotImplementedError()

    def save_chunk(self, chunk_dict):
        raise NotImplementedError()

    def load_chunks_for_bundle(self, bundle_id):
        raise NotImplementedError()

    def save_custody_record(self, custody_dict):
        raise NotImplementedError()

    def load_custody_record(self, bundle_id):
        raise NotImplementedError()

    def list_bundles(self):
        raise NotImplementedError()