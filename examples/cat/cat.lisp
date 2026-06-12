(defun cat ()
    (declare (interrupt 0))
    (output 1 (input 0)))

(defun run () (run))

(run)