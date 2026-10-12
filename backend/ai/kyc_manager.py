class KYCManager:
    def __init__(self, db):
        self.db = db

    def update_basic_info(self, user_id, full_name, phone, email, dob, id_number):
        query = '''UPDATE users SET full_name=%s, phone=%s, email=%s,
                   date_of_birth=%s, id_number=%s, kyc_level=GREATEST(kyc_level, 1)
                   WHERE id=%s'''
        return self.db._execute_query(query, (full_name, phone, email, dob, id_number, user_id), commit=True)

    def insert_document(self, user_id, doc_type, file_path):
        query = '''INSERT INTO kyc_documents (user_id, doc_type, file_path)
                   VALUES (%s, %s, %s)
                   ON CONFLICT DO NOTHING'''
        return self.db._execute_query(query, (user_id, doc_type, file_path), commit=True)

    def delete_document(self, user_id, doc_type):
        query = "DELETE FROM kyc_documents WHERE user_id=%s AND doc_type=%s"
        return self.db._execute_query(query, (user_id, doc_type), commit=True)

    def get_documents(self, user_id):
        query = "SELECT * FROM kyc_documents WHERE user_id=%s ORDER BY uploaded_at"
        return self.db._execute_query(query, (user_id,), fetchall=True, commit=False)

    def update_level(self, user_id, level):
        if level == 3:
            query = "UPDATE users SET kyc_level=%s, kyc_submitted_at=NOW() WHERE id=%s"
        else:
            query = "UPDATE users SET kyc_level=%s WHERE id=%s"
        return self.db._execute_query(query, (level, user_id), commit=True)

    def get_user_detail(self, user_id):
        query = '''SELECT u.id, u.username, u.full_name, u.phone, u.email,
                          u.date_of_birth, u.id_number, u.kyc_level,
                          u.kyc_submitted_at, u.kyc_reviewed_at,
                          u.kyc_reviewed_by, u.kyc_reject_reason,
                          r.role_name as role
                   FROM users u LEFT JOIN roles r ON u.role_id = r.id
                   WHERE u.id=%s'''
        return self.db._execute_query(query, (user_id,), fetch=True, commit=False)

    def get_pending_users(self):
        query = '''SELECT u.id, u.username, u.full_name, u.phone, u.email,
                          u.id_number, u.kyc_level, u.kyc_submitted_at,
                          r.role_name as role
                   FROM users u LEFT JOIN roles r ON u.role_id = r.id
                   WHERE u.kyc_level = 3
                   ORDER BY u.kyc_submitted_at'''
        return self.db._execute_query(query, fetchall=True, commit=False)

    def approve(self, user_id, reviewer):
        self.db._execute_query(
            '''UPDATE users SET kyc_level=4, kyc_reviewed_at=NOW(),
               kyc_reviewed_by=%s, kyc_reject_reason=NULL WHERE id=%s''',
            (reviewer, user_id), commit=True
        )
        return self.db._execute_query(
            "UPDATE kyc_documents SET status='approved', reviewed_at=NOW(), reviewed_by=%s WHERE user_id=%s",
            (reviewer, user_id), commit=True
        )

    def reject(self, user_id, reviewer, reason):
        self.db._execute_query(
            '''UPDATE users SET kyc_level=-1, kyc_reviewed_at=NOW(),
               kyc_reviewed_by=%s, kyc_reject_reason=%s WHERE id=%s''',
            (reviewer, reason, user_id), commit=True
        )
        return self.db._execute_query(
            "UPDATE kyc_documents SET status='rejected', reviewed_at=NOW(), reviewed_by=%s WHERE user_id=%s",
            (reviewer, user_id), commit=True
        )

    def reset(self, user_id):
        self.db._execute_query(
            '''UPDATE users SET kyc_level=0, kyc_submitted_at=NULL,
               kyc_reviewed_at=NULL, kyc_reviewed_by=NULL, kyc_reject_reason=NULL
               WHERE id=%s''',
            (user_id,), commit=True
        )
        return self.db._execute_query("DELETE FROM kyc_documents WHERE user_id=%s", (user_id,), commit=True)
